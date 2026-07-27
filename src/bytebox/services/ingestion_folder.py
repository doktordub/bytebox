"""Folder-scoped ingestion workflows and manifest helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from ..errors import IngestionError, MemoryStoreError
from ..ingest_security import collect_markdown_files, default_ingest_manifest_path, resolve_ingest_path
from ..models import (
    FolderIngestConnectionStrategy,
    FolderIngestManifest,
    FolderIngestManifestEntry,
    FolderIngestManifestStatus,
    FolderIngestResult,
    IngestCounters,
    IngestResult,
    IngestTimings,
    Scope,
)
from .ingestion_document import DocumentIngestWorker, _elapsed_ms


class FolderIngestWorker:
    """Owns multi-document ingestion orchestration and manifest state."""

    def __init__(self, owner: Any, documents: DocumentIngestWorker) -> None:
        self._owner = owner
        self._documents = documents

    def ingest_folder(
        self,
        path: str | Path,
        scope: Scope,
        *,
        stop_on_error: bool = False,
        continue_on_error: bool | None = None,
        resume_from: str | Path | None = None,
        connection_strategy: FolderIngestConnectionStrategy | str = (
            FolderIngestConnectionStrategy.REOPEN_ON_FAILURE
        ),
        dry_run: bool = False,
        manifest_path: str | Path | None = None,
        only_failed: bool = False,
        limit: int | None = None,
        since: datetime | str | None = None,
        progress_every_documents: int = 0,
        progress_every_chunks: int = 0,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FolderIngestResult:
        root = resolve_ingest_path(
            path,
            ingest_roots=self._owner.settings.security.ingest_roots,
            allow_symlinks=self._owner.settings.security.allow_symlinks,
            expect_directory=True,
        )

        resolved_stop_on_error = self._resolve_stop_on_error(
            stop_on_error=stop_on_error,
            continue_on_error=continue_on_error,
        )
        strategy = FolderIngestConnectionStrategy(connection_strategy)
        since_value = self._coerce_since(since)
        manifest_target = self._resolve_manifest_path(root, manifest_path)
        available_files = self._collect_markdown_files(
            root,
            resume_from=resume_from,
            since=since_value,
        )
        manifest = self._load_ingest_manifest(root, manifest_target)
        self._synchronize_manifest_with_files(manifest, root, available_files)
        markdown_files = self._select_ingest_candidates(
            available_files,
            manifest,
            only_failed=only_failed,
            limit=limit,
        )
        persist_manifest = not dry_run
        if persist_manifest:
            self._write_ingest_manifest(manifest_target, manifest)

        result = FolderIngestResult(
            root=root,
            connection_strategy=strategy,
            manifest_path=manifest_target,
            resume_from=self._normalize_resume_from(root, resume_from),
            stop_on_error=resolved_stop_on_error,
            only_failed=only_failed,
            limit=limit,
            since=since_value,
            matched_files=len(markdown_files),
            skipped_files=max(0, len(available_files) - len(markdown_files)),
            status_counts=self._manifest_status_counts(manifest),
        )
        for markdown_file in markdown_files:
            relative_path = markdown_file.relative_to(root)
            if persist_manifest:
                self._set_manifest_entry_status(
                    manifest,
                    relative_path,
                    status=FolderIngestManifestStatus.RUNNING,
                    attempts_delta=1,
                )
                self._write_ingest_manifest(manifest_target, manifest)

            try:
                ingest_kwargs: dict[str, Any] = {}
                if dry_run:
                    ingest_kwargs["dry_run"] = True
                if progress_callback is not None and progress_every_chunks > 0:
                    ingest_kwargs["progress_every_chunks"] = progress_every_chunks
                    ingest_kwargs["progress_callback"] = self._wrap_document_progress_callback(
                        progress_callback,
                        relative_path,
                    )

                file_result = self._owner.ingest_document(
                    markdown_file,
                    scope,
                    **ingest_kwargs,
                ).model_copy(update={"path": relative_path})

                if not dry_run and strategy is FolderIngestConnectionStrategy.REOPEN_PER_FILE:
                    close_started_at = perf_counter()
                    self._owner.close()
                    self._owner._verify_lock_released()
                    close_ms = _elapsed_ms(close_started_at)
                    file_result.timings.close_ms += close_ms
                    file_result.timings.elapsed_ms += close_ms

                result.files.append(file_result)
                result.files_processed += 1
                result.added += file_result.added
                result.updated += file_result.updated
                result.removed += file_result.removed
                result.unchanged += file_result.unchanged
                if persist_manifest:
                    self._record_manifest_result(manifest, file_result)
                    self._write_ingest_manifest(manifest_target, manifest)
            except MemoryStoreError as exc:
                file_result = self._owner._build_failed_ingest_result(relative_path, exc)
                if (
                    not dry_run
                    and self._owner._should_recover_after_failure(strategy, exc)
                    and not resolved_stop_on_error
                ):
                    close_started_at = perf_counter()
                    self._owner.close()
                    self._owner._verify_lock_released()
                    self._owner._ensure_repository()
                    close_ms = _elapsed_ms(close_started_at)
                    file_result.timings.close_ms += close_ms
                    file_result.timings.elapsed_ms += close_ms

                result.files.append(file_result)
                result.files_processed += 1
                result.failed_files += 1
                result.ok = False
                if persist_manifest:
                    self._record_manifest_result(manifest, file_result)
                    self._write_ingest_manifest(manifest_target, manifest)
                if resolved_stop_on_error:
                    result.stopped_on_error = True
                    break

            if progress_callback is not None and progress_every_documents > 0:
                if (
                    result.files_processed % progress_every_documents == 0
                    or result.files_processed == result.matched_files
                ):
                    progress_callback(
                        {
                            "kind": "document",
                            "path": relative_path.as_posix(),
                            "processed_files": result.files_processed,
                            "total_files": result.matched_files,
                            "failed_files": result.failed_files,
                            "ok": file_result.ok,
                        }
                    )

        result.ok = result.failed_files == 0
        result.status_counts = self._manifest_status_counts(manifest)
        if persist_manifest:
            self._write_ingest_manifest(manifest_target, manifest)
        return result

    def _collect_markdown_files(
        self,
        root: Path,
        *,
        resume_from: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[Path]:
        markdown_files = sorted(
            collect_markdown_files(
                root,
                allow_symlinks=self._owner.settings.security.allow_symlinks,
            )
        )
        if since is not None:
            markdown_files = [
                file_path
                for file_path in markdown_files
                if (modified_at := self._documents._file_modified_at(file_path)) is not None
                and modified_at >= since
            ]
        resume_label = self._normalize_resume_from(root, resume_from)
        if resume_label is None:
            return markdown_files

        labels = [file_path.relative_to(root).as_posix() for file_path in markdown_files]
        if resume_label not in labels:
            raise IngestionError(
                f"resume_from did not match a Markdown file under {root}: {resume_label}"
            )
        return markdown_files[labels.index(resume_label) :]

    def _normalize_resume_from(
        self,
        root: Path,
        resume_from: str | Path | None,
    ) -> str | None:
        if resume_from is None:
            return None

        candidate = Path(resume_from)
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return candidate.as_posix()
        return candidate.as_posix().lstrip("./")

    def _resolve_stop_on_error(
        self,
        *,
        stop_on_error: bool,
        continue_on_error: bool | None,
    ) -> bool:
        if continue_on_error is None:
            return stop_on_error
        return not continue_on_error

    def _coerce_since(self, since: datetime | str | None) -> datetime | None:
        if since is None:
            return None
        if isinstance(since, datetime):
            return since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)

        value = since.strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IngestionError(f"Invalid since value: {since}") from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _resolve_manifest_path(self, root: Path, manifest_path: str | Path | None) -> Path:
        if manifest_path is None:
            return default_ingest_manifest_path(
                root,
                state_dir=self._owner.settings.application.state_dir,
            )
        candidate = Path(manifest_path)
        if candidate.is_absolute():
            return candidate
        return root / candidate

    def _load_ingest_manifest(self, root: Path, manifest_path: Path) -> FolderIngestManifest:
        if not manifest_path.exists():
            return FolderIngestManifest(root=root)

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IngestionError(f"Failed to read ingest manifest {manifest_path}: {exc}") from exc

        try:
            manifest = FolderIngestManifest.model_validate(payload)
        except Exception as exc:
            raise IngestionError(f"Invalid ingest manifest {manifest_path}: {exc}") from exc

        if manifest.root.resolve(strict=False) != root.resolve(strict=False):
            raise IngestionError(
                f"Ingest manifest {manifest_path} belongs to {manifest.root}, not {root}."
            )
        return manifest

    def _write_ingest_manifest(self, manifest_path: Path, manifest: FolderIngestManifest) -> None:
        manifest.updated_at = self._owner._utcnow()
        manifest.status_counts = self._manifest_status_counts(manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(manifest_path)

    def _synchronize_manifest_with_files(
        self,
        manifest: FolderIngestManifest,
        root: Path,
        markdown_files: list[Path],
    ) -> None:
        entries = self._manifest_entry_map(manifest)

        for entry in list(entries.values()):
            if entry.status is FolderIngestManifestStatus.RUNNING:
                entry.status = FolderIngestManifestStatus.PENDING

        for file_path in markdown_files:
            relative_path = file_path.relative_to(root)
            label = relative_path.as_posix()
            modified_at = self._documents._file_modified_at(file_path)
            entry = entries.get(label)
            if entry is None:
                entries[label] = FolderIngestManifestEntry(
                    path=relative_path,
                    file_size_bytes=self._documents._measure_file_size_bytes(file_path),
                    modified_at=modified_at,
                )
                continue

            entry.path = relative_path
            entry.file_size_bytes = self._documents._measure_file_size_bytes(file_path)
            entry.modified_at = modified_at

        manifest.files = sorted(entries.values(), key=lambda item: item.path.as_posix())
        manifest.status_counts = self._manifest_status_counts(manifest)

    def _select_ingest_candidates(
        self,
        markdown_files: list[Path],
        manifest: FolderIngestManifest,
        *,
        only_failed: bool,
        limit: int | None,
    ) -> list[Path]:
        statuses = {entry.path.as_posix(): entry.status for entry in manifest.files}
        selected: list[Path] = []
        for file_path in markdown_files:
            status = statuses.get(file_path.relative_to(manifest.root).as_posix())
            if status is FolderIngestManifestStatus.SUCCEEDED:
                continue
            if only_failed and status is not FolderIngestManifestStatus.FAILED:
                continue
            selected.append(file_path)

        if limit is not None:
            return selected[:limit]
        return selected

    def _manifest_entry_map(
        self,
        manifest: FolderIngestManifest,
    ) -> dict[str, FolderIngestManifestEntry]:
        return {entry.path.as_posix(): entry for entry in manifest.files}

    def _set_manifest_entry_status(
        self,
        manifest: FolderIngestManifest,
        relative_path: Path,
        *,
        status: FolderIngestManifestStatus,
        attempts_delta: int = 0,
        error: str | None = None,
        exception_class: str | None = None,
        counters: IngestCounters | None = None,
        timings: IngestTimings | None = None,
    ) -> None:
        entries = self._manifest_entry_map(manifest)
        key = relative_path.as_posix()
        entry = entries.get(key)
        if entry is None:
            entry = FolderIngestManifestEntry(path=relative_path)
            manifest.files.append(entry)

        entry.path = relative_path
        entry.status = status
        entry.attempts += attempts_delta
        entry.updated_at = self._owner._utcnow()
        entry.error = error
        entry.exception_class = exception_class
        if counters is not None:
            entry.counters = counters
        if timings is not None:
            entry.timings = timings
        manifest.files.sort(key=lambda item: item.path.as_posix())
        manifest.status_counts = self._manifest_status_counts(manifest)

    def _record_manifest_result(
        self,
        manifest: FolderIngestManifest,
        result: IngestResult,
    ) -> None:
        self._set_manifest_entry_status(
            manifest,
            result.path,
            status=(
                FolderIngestManifestStatus.SUCCEEDED
                if result.ok
                else FolderIngestManifestStatus.FAILED
            ),
            error=result.error,
            exception_class=result.exception_class,
            counters=result.counters,
            timings=result.timings,
        )

    def _manifest_status_counts(self, manifest: FolderIngestManifest) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in manifest.files:
            counts[entry.status.value] = counts.get(entry.status.value, 0) + 1
        return counts

    def _wrap_document_progress_callback(
        self,
        callback: Callable[[dict[str, Any]], None],
        relative_path: Path,
    ) -> Callable[[dict[str, Any]], None]:
        def _wrapped(event: dict[str, Any]) -> None:
            payload = dict(event)
            payload["path"] = relative_path.as_posix()
            callback(payload)

        return _wrapped