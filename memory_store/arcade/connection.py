"""ArcadeDB embedded connection and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
import json
import os
from pathlib import Path
import socket
from typing import TYPE_CHECKING, Any

from ..config import DatabaseSettings
from ..errors import PersistenceError

if TYPE_CHECKING:
    from arcadedb_embedded import Database
else:
    Database = Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ArcadeLockMetadata:
    pid: int
    hostname: str | None = None
    created_at: str | None = None
    process_start: str | None = None

    def to_payload(self) -> bytes:
        return json.dumps(
            {
                "pid": self.pid,
                "hostname": self.hostname,
                "created_at": self.created_at,
                "process_start": self.process_start,
            },
            sort_keys=True,
        ).encode("utf-8")


def _current_lock_metadata() -> ArcadeLockMetadata:
    pid = os.getpid()
    return ArcadeLockMetadata(
        pid=pid,
        hostname=socket.gethostname(),
        created_at=_utcnow_iso(),
        process_start=_best_effort_process_start(pid),
    )


def _read_lock_metadata(lock_path: Path) -> ArcadeLockMetadata | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not raw:
        return None

    data: dict[str, Any]
    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        data = loaded
    else:
        data = {}
        for line in raw.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()

    pid = data.get("pid")
    try:
        resolved_pid = int(pid)
    except (TypeError, ValueError):
        return None

    hostname = data.get("hostname")
    created_at = data.get("created_at")
    process_start = data.get("process_start")
    return ArcadeLockMetadata(
        pid=resolved_pid,
        hostname=str(hostname) if hostname is not None else None,
        created_at=str(created_at) if created_at is not None else None,
        process_start=str(process_start) if process_start is not None else None,
    )


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _best_effort_process_start(pid: int) -> str | None:
    if pid <= 0:
        return None

    proc_stat = Path("/proc") / str(pid) / "stat"
    if proc_stat.exists():
        try:
            payload = proc_stat.read_text(encoding="utf-8")
        except OSError:
            payload = ""
        if payload:
            delimiter = payload.rfind(")")
            if delimiter != -1:
                fields = payload[delimiter + 2 :].split()
                if len(fields) > 19:
                    return fields[19]

    if os.name == "nt":
        return _best_effort_windows_process_start(pid)
    return None


def _best_effort_windows_process_start(pid: int) -> str | None:
    if os.name != "nt":  # pragma: no cover - Windows-specific branch
        return None

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - ctypes is part of stdlib
        return None

    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return None

    try:
        creation_time = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation_time),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None

        return str((creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _lock_owner_is_alive(metadata: ArcadeLockMetadata) -> bool:
    if not _process_exists(metadata.pid):
        return False
    if metadata.process_start is None:
        return True

    actual_process_start = _best_effort_process_start(metadata.pid)
    if actual_process_start is None:
        return True
    return actual_process_start == metadata.process_start


def _format_lock_owner(metadata: ArcadeLockMetadata | None) -> str:
    if metadata is None:
        return "unknown owner"

    parts = [f"pid={metadata.pid}"]
    if metadata.hostname:
        parts.append(f"host={metadata.hostname}")
    if metadata.created_at:
        parts.append(f"created_at={metadata.created_at}")
    return ", ".join(parts)


@dataclass(slots=True)
class ArcadeConnectionSettings:
    path: Path
    create_if_missing: bool = True
    embedded_single_process: bool = True

    @classmethod
    def from_database_settings(cls, settings: DatabaseSettings) -> "ArcadeConnectionSettings":
        return cls(
            path=settings.path,
            create_if_missing=settings.create_if_missing,
            embedded_single_process=settings.embedded_single_process,
        )


@dataclass(slots=True)
class ArcadeDatabaseHandle:
    settings: ArcadeConnectionSettings
    database_path: Path
    database: Database
    _lock: "ArcadeProcessLock | None" = None

    def close(self) -> None:
        close_error: Exception | None = None
        try:
            self.database.close()
        except Exception as exc:  # pragma: no cover - wrapped by caller in tests
            close_error = exc
        finally:
            if self._lock is not None:
                self._lock.release()

        if close_error is not None:
            raise PersistenceError(f"Failed to close ArcadeDB database: {close_error}") from close_error

    def __enter__(self) -> "ArcadeDatabaseHandle":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()


class ArcadeProcessLock:
    """Best-effort single-process guard for embedded database usage."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        try:
            self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            if self._reclaim_stale_lock():
                try:
                    self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                except FileExistsError as retry_exc:
                    owner = _format_lock_owner(_read_lock_metadata(self._lock_path))
                    raise PersistenceError(
                        "ArcadeDB path is already locked by another process: "
                        f"{self._lock_path} ({owner})"
                    ) from retry_exc
            else:
                owner = _format_lock_owner(_read_lock_metadata(self._lock_path))
                raise PersistenceError(
                    "ArcadeDB path is already locked by another process: "
                    f"{self._lock_path} ({owner})"
                ) from exc

        payload = _current_lock_metadata().to_payload()
        try:
            os.write(self._fd, payload)
        except OSError:
            self.release()
            raise

    def _reclaim_stale_lock(self) -> bool:
        metadata = _read_lock_metadata(self._lock_path)
        if metadata is None or _lock_owner_is_alive(metadata):
            return False

        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            return


def arcade_runtime_available() -> bool:
    return find_spec("arcadedb_embedded") is not None


def normalize_database_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def unlock_arcade_database(path: str | Path, *, force: bool = False) -> dict[str, Any]:
    database_path = normalize_database_path(path)
    lock_path = database_path.parent / f"{database_path.name}.lock"
    metadata = _read_lock_metadata(lock_path)
    lock_exists = lock_path.exists()
    stale_owner = metadata is not None and not _lock_owner_is_alive(metadata)

    if not lock_exists:
        return {
            "lock_path": str(lock_path),
            "removed": False,
            "force": force,
            "stale_owner": stale_owner,
            "owner": _serialize_lock_owner(metadata),
        }

    if not force:
        if metadata is None:
            raise PersistenceError(
                f"Lock owner metadata could not be read from {lock_path}; rerun with force=True."
            )
        if _lock_owner_is_alive(metadata):
            raise PersistenceError(
                "Refusing to remove a live ArcadeDB lock: "
                f"{lock_path} ({_format_lock_owner(metadata)})"
            )

    try:
        lock_path.unlink(missing_ok=True)
    except OSError as exc:
        raise PersistenceError(f"Failed to remove ArcadeDB lock {lock_path}: {exc}") from exc

    return {
        "lock_path": str(lock_path),
        "removed": True,
        "force": force,
        "stale_owner": stale_owner,
        "owner": _serialize_lock_owner(metadata),
    }


def _serialize_lock_owner(metadata: ArcadeLockMetadata | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "pid": metadata.pid,
        "hostname": metadata.hostname,
        "created_at": metadata.created_at,
        "process_start": metadata.process_start,
    }


def open_arcade_database(settings: ArcadeConnectionSettings) -> ArcadeDatabaseHandle:
    if not arcade_runtime_available():
        raise PersistenceError("arcadedb_embedded is not installed in the current environment.")

    from arcadedb_embedded import DatabaseFactory

    database_path = normalize_database_path(settings.path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    lock = None
    if settings.embedded_single_process:
        lock = ArcadeProcessLock(database_path.parent / f"{database_path.name}.lock")
        lock.acquire()

    try:
        factory = DatabaseFactory(str(database_path))
        if factory.exists():
            database = factory.open()
        elif settings.create_if_missing:
            database = factory.create()
        else:
            raise PersistenceError(f"ArcadeDB database does not exist: {database_path}")
    except Exception as exc:
        if lock is not None:
            lock.release()
        raise PersistenceError(f"Failed to open ArcadeDB database at {database_path}: {exc}") from exc

    return ArcadeDatabaseHandle(
        settings=settings,
        database_path=database_path,
        database=database,
        _lock=lock,
    )
