"""Legacy mem-store configuration migration helpers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import ConfigError

LEGACY_ENV_PREFIX = "MEMORY_STORE_"
CURRENT_ENV_PREFIX = "BYTEBOX_"
_YAML_SUFFIXES = {".yaml", ".yml"}
_ENV_SUFFIXES = {".env", ".txt", ".properties"}
_SECRET_ENV_TARGETS = {
    "BYTEBOX_API__LOCAL_API_TOKEN": "BYTEBOX_API__LOCAL_API_TOKEN",
    "BYTEBOX_API__ROTATED_API_TOKENS": "BYTEBOX_API__ROTATED_API_TOKENS",
    "BYTEBOX_API__TLS__KEY_PASSWORD": "BYTEBOX_API__TLS__KEY_PASSWORD",
    "BYTEBOX_EMBEDDINGS__REMOTE__CLIENT_KEY_PASSWORD": (
        "BYTEBOX_EMBEDDINGS__REMOTE__CLIENT_KEY_PASSWORD"
    ),
    "BYTEBOX_RERANKER__REMOTE__CLIENT_KEY_PASSWORD": (
        "BYTEBOX_RERANKER__REMOTE__CLIENT_KEY_PASSWORD"
    ),
}


def migrate_legacy_config(
    source: str | Path,
    *,
    out: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    source_path = Path(source)
    if not source_path.exists():
        raise ConfigError(f"Legacy config source does not exist: {source_path}")

    source_format = _detect_source_format(source_path)
    output_path = Path(out) if out is not None else None
    actions: list[dict[str, Any]] = []

    if source_format == "yaml":
        preview = _migrate_yaml_file(source_path, actions)
        if output_path is not None and not dry_run:
            _write_yaml(output_path, preview)
    else:
        preview = _migrate_env_file(source_path, actions)
        if output_path is not None and not dry_run:
            _write_env_file(output_path, preview)

    warnings = _build_warnings(preview if isinstance(preview, Mapping) else {}, source_format)
    return {
        "source_path": str(source_path),
        "source_format": source_format,
        "output_path": str(output_path) if output_path is not None else None,
        "dry_run": dry_run,
        "actions": actions,
        "warnings": warnings,
        "preview": preview,
    }


def _detect_source_format(source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        return "yaml"
    if suffix in _ENV_SUFFIXES or source_path.name.lower().endswith(".env"):
        return "env"
    raise ConfigError(
        "Unsupported legacy config source. Use a .yaml/.yml file or a .env-style file."
    )


def _migrate_yaml_file(source_path: Path, actions: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _load_yaml(source_path)
    return _migrate_yaml_mapping(payload, actions)


def _migrate_yaml_mapping(
    payload: Mapping[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    migrated = _normalize_mapping(payload)

    legacy_root = migrated.pop("memory_store", None)
    if isinstance(legacy_root, Mapping):
        merged = deepcopy(_normalize_mapping(legacy_root))
        merged.update(migrated)
        migrated = merged
        actions.append(
            {
                "kind": "lift_legacy_root",
                "source": "memory_store",
                "target": "<root>",
            }
        )

    _rewrite_yaml_path(migrated, actions, ("database", "path"))
    _rewrite_yaml_path(migrated, actions, ("application", "state_dir"))

    _remove_secret_path(
        migrated,
        actions,
        ("api", "local_api_token"),
        target_env="BYTEBOX_API__LOCAL_API_TOKEN",
    )
    _remove_secret_path(
        migrated,
        actions,
        ("api", "rotated_api_tokens"),
        target_env="BYTEBOX_API__ROTATED_API_TOKENS",
    )
    _remove_secret_path(
        migrated,
        actions,
        ("api", "tls", "key_password"),
        target_env="BYTEBOX_API__TLS__KEY_PASSWORD",
    )
    _remove_secret_path(
        migrated,
        actions,
        ("embeddings", "remote", "client_key_password"),
        target_env="BYTEBOX_EMBEDDINGS__REMOTE__CLIENT_KEY_PASSWORD",
    )
    _remove_secret_path(
        migrated,
        actions,
        ("reranker", "remote", "client_key_password"),
        target_env="BYTEBOX_RERANKER__REMOTE__CLIENT_KEY_PASSWORD",
    )
    _remove_secret_path(
        migrated,
        actions,
        ("api", "auth", "tokens"),
        target_env="BYTEBOX_API__LOCAL_API_TOKEN",
        reason=(
            "Legacy token lists are not copied into the migrated file. Re-provision tokens "
            "out-of-band after the file migration succeeds."
        ),
    )
    return migrated


def _migrate_env_file(source_path: Path, actions: list[dict[str, Any]]) -> dict[str, str]:
    migrated: dict[str, str] = {}
    for key, value in _load_env_file(source_path).items():
        target_key = key
        if key.startswith(LEGACY_ENV_PREFIX):
            target_key = CURRENT_ENV_PREFIX + key.removeprefix(LEGACY_ENV_PREFIX)
            actions.append(
                {
                    "kind": "rename_env_prefix",
                    "source": key,
                    "target": target_key,
                }
            )

        if target_key in _SECRET_ENV_TARGETS:
            actions.append(
                {
                    "kind": "remove_secret",
                    "source": key,
                    "target": _SECRET_ENV_TARGETS[target_key],
                    "reason": "Secret values are never copied into migrated config output.",
                }
            )
            continue

        if target_key in {"BYTEBOX_DATABASE__PATH", "BYTEBOX_APPLICATION__STATE_DIR"}:
            rewritten = _rewrite_legacy_path_value(value)
            if rewritten != value:
                actions.append(
                    {
                        "kind": "rewrite_path",
                        "source": key,
                        "target": target_key,
                    }
                )
            value = rewritten

        migrated[target_key] = value

    return migrated


def _build_warnings(preview: Mapping[str, Any], source_format: str) -> list[str]:
    warnings: list[str] = []
    if source_format == "yaml":
        security = preview.get("security")
        api = preview.get("api")
        api_enabled = isinstance(api, Mapping) and bool(api.get("enabled"))
        ingest_roots = []
        if isinstance(security, Mapping):
            roots = security.get("ingest_roots")
            if isinstance(roots, list):
                ingest_roots = [str(item) for item in roots if str(item).strip()]
        if api_enabled and not ingest_roots:
            warnings.append(
                "Review security.ingest_roots before enabling REST ingestion in production."
            )

    warnings.append(
        "Review strict offline model settings before cutover: configure local model paths, "
        "local_files_only, and hf_hub_offline as needed."
    )
    return warnings


def _remove_secret_path(
    payload: dict[str, Any],
    actions: list[dict[str, Any]],
    path: tuple[str, ...],
    *,
    target_env: str,
    reason: str | None = None,
) -> None:
    removed = _pop_nested(payload, path)
    if removed is None:
        return
    actions.append(
        {
            "kind": "remove_secret",
            "source": ".".join(path),
            "target": target_env,
            "reason": reason or "Secret values are never copied into migrated config output.",
        }
    )


def _rewrite_yaml_path(
    payload: dict[str, Any],
    actions: list[dict[str, Any]],
    path: tuple[str, ...],
) -> None:
    current = _get_nested(payload, path)
    if not isinstance(current, str):
        return
    rewritten = _rewrite_legacy_path_value(current)
    if rewritten == current:
        return
    _set_nested(payload, path, rewritten)
    actions.append(
        {
            "kind": "rewrite_path",
            "source": ".".join(path),
            "target": ".".join(path),
        }
    )


def _rewrite_legacy_path_value(value: str) -> str:
    return (
        value.replace("memory_store", "bytebox")
        .replace("mem_store", "bytebox")
        .replace("mem-store", "bytebox")
    )


def _load_yaml(source_path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError("PyYAML is required to migrate YAML config files.") from exc

    loaded = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Legacy YAML config must contain a mapping at the top level.")
    return _normalize_mapping(loaded)


def _write_yaml(destination: Path, payload: Mapping[str, Any]) -> None:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError("PyYAML is required to write migrated YAML config files.") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False),
        encoding="utf-8",
    )


def _load_env_file(source_path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _write_env_file(destination: Path, payload: Mapping[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in sorted(payload.items())]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _normalize_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_mapping(item) for item in value]
    return value


def _get_nested(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_nested(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = payload
    for segment in path[:-1]:
        nested = current.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            current[segment] = nested
        current = nested
    current[path[-1]] = value


def _pop_nested(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for segment in path[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    if not isinstance(current, dict):
        return None
    return current.pop(path[-1], None)