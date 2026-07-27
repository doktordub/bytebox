"""Operational database inspection, migration, and re-embedding helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .arcade import (
    ArcadeMemoryRepository,
    arcade_runtime_available,
    backup_arcade_database,
    open_arcade_database,
    plan_database_migrations,
    read_schema_version,
    run_database_migrations,
)
from .arcade.connection import ArcadeConnectionSettings, normalize_database_path
from .arcade.schema import EDGE_TYPES, MEMORY_RECORD_VERTEX
from .config import MemoryStoreSettings
from .embeddings import (
    FastEmbedProvider,
    LlamaCppEmbeddingProvider,
    OllamaEmbeddingProvider,
    ProviderRegistry,
    build_embedding_text,
    read_active_index_dimension,
)
from .errors import PersistenceError
from .models import MemoryRecord, MemorySearchQuery, Scope
from .store import MemoryStore


def derive_target_database_path(source_database_path: str | Path) -> Path:
    source_path = normalize_database_path(source_database_path)
    source_name = source_path.name
    if "memory_store" in source_name:
        target_name = source_name.replace("memory_store", "bytebox")
    elif "mem_store" in source_name:
        target_name = source_name.replace("mem_store", "bytebox")
    elif "mem-store" in source_name:
        target_name = source_name.replace("mem-store", "bytebox")
    else:
        target_name = f"{source_name}-bytebox"
    return source_path.with_name(target_name)


def inspect_database(
    database_path: str | Path,
    *,
    settings: MemoryStoreSettings,
) -> dict[str, Any]:
    resolved_path = normalize_database_path(database_path)
    lock_path = resolved_path.parent / f"{resolved_path.name}.lock"
    payload: dict[str, Any] = {
        "database_path": str(resolved_path),
        "exists": resolved_path.exists(),
        "lock_path": str(lock_path),
        "lock_exists": lock_path.exists(),
        "runtime_available": arcade_runtime_available(),
        "configured_schema_version": settings.database.schema_version,
        "configured_embedding_dimension": settings.embeddings.dim,
        "issues": [],
    }
    if not resolved_path.exists() or not payload["runtime_available"]:
        if resolved_path.exists() and not payload["runtime_available"]:
            payload["issues"].append(
                "ArcadeDB runtime is not available in the current environment."
            )
        return payload

    try:
        snapshot = _collect_database_snapshot(resolved_path, settings)
    except PersistenceError as exc:
        payload["issues"].append(str(exc))
        payload["openable"] = False
        return payload

    payload.update(snapshot)
    payload["openable"] = True
    return payload


def verify_database(
    database_path: str | Path,
    *,
    settings: MemoryStoreSettings,
    search_queries: Sequence[str] = (),
    scope: Scope | None = None,
) -> dict[str, Any]:
    _ensure_runtime_available()
    resolved_path = normalize_database_path(database_path)
    if not resolved_path.exists():
        raise PersistenceError(f"ArcadeDB database does not exist: {resolved_path}")

    snapshot = _collect_database_snapshot(resolved_path, settings)
    issues = list(snapshot.get("issues", []))
    active_dimension = snapshot.get("active_embedding_dimension")
    configured_dimension = settings.embeddings.dim
    if configured_dimension is not None and active_dimension not in {None, configured_dimension}:
        issues.append(
            "Configured embedding dimension does not match the active vector index dimension."
        )

    configured_identity = _configured_embedding_identity(settings)
    embedding_identities = snapshot.get("embedding_identities", [])
    if embedding_identities:
        distinct_identities = {
            (
                entry.get("model_name"),
                entry.get("model_version"),
                entry.get("embedding_dim"),
            )
            for entry in embedding_identities
        }
        if len(distinct_identities) > 1:
            issues.append("Multiple stored embedding identities were detected in the database.")

        for entry in embedding_identities:
            if configured_dimension is not None and entry.get("embedding_dim") not in {
                None,
                configured_dimension,
            }:
                issues.append(
                    "Stored embedding dimensions do not match the configured embedding dimension."
                )
                break
        for entry in embedding_identities:
            if entry.get("model_name") not in {None, configured_identity["model_name"]}:
                issues.append(
                    "Stored embedding model identities do not match the configured embedding model."
                )
                break

    search_checks = _run_search_checks(
        resolved_path,
        settings=settings,
        search_queries=search_queries,
        scope=scope,
    )
    for check in search_checks:
        if not check["ok"]:
            issues.append(
                f"Representative search returned no results: {check['query']}"
            )

    return {
        **snapshot,
        "database_path": str(resolved_path),
        "configured_embedding_identity": configured_identity,
        "search_checks": search_checks,
        "ok": len(issues) == 0,
        "issues": _deduplicate_strings(issues),
    }


def migrate_database(
    source_database_path: str | Path,
    *,
    settings: MemoryStoreSettings,
    target_database_path: str | Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    backup_destination: str | Path | None = None,
    search_queries: Sequence[str] = (),
    verification_scope: Scope | None = None,
) -> dict[str, Any]:
    _ensure_runtime_available()
    source_path = normalize_database_path(source_database_path)
    if not source_path.exists():
        raise PersistenceError(f"ArcadeDB database does not exist: {source_path}")

    target_path = normalize_database_path(
        target_database_path or derive_target_database_path(source_path)
    )
    if target_path == source_path:
        raise PersistenceError(
            "Phase 10 migration is non-destructive by default; choose a target path different "
            "from the source database path."
        )

    source_inspection = inspect_database(source_path, settings=settings)
    if dry_run:
        return {
            "dry_run": True,
            "source_database_path": str(source_path),
            "target_database_path": str(target_path),
            "backup_required": True,
            "source_inspection": source_inspection,
        }

    backup_summary = backup_arcade_database(
        source_path,
        destination=backup_destination,
        overwrite=overwrite,
    )
    copy_summary = backup_arcade_database(
        source_path,
        destination=target_path,
        overwrite=overwrite,
    )

    handle = open_arcade_database(
        ArcadeConnectionSettings(
            path=target_path,
            create_if_missing=False,
            embedded_single_process=settings.database.embedded_single_process,
        )
    )
    try:
        migration_result = run_database_migrations(
            handle.database,
            database_path=target_path,
            expected_version=settings.database.schema_version,
            embedding_dimensions=_required_embedding_dimension(settings),
            dry_run=False,
            create_backup=False,
        )
    finally:
        handle.close()

    verification = verify_database(
        target_path,
        settings=settings,
        search_queries=search_queries,
        scope=verification_scope,
    )
    return {
        "dry_run": False,
        "source_database_path": str(source_path),
        "target_database_path": str(target_path),
        "backup": backup_summary,
        "copied_source": copy_summary,
        "migration": _serialize_migration_result(migration_result),
        "verification": verification,
    }


def reembed_database(
    database_path: str | Path,
    *,
    settings: MemoryStoreSettings,
    scope: Scope | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    _ensure_runtime_available()
    resolved_path = normalize_database_path(database_path)
    if not resolved_path.exists():
        raise PersistenceError(f"ArcadeDB database does not exist: {resolved_path}")

    provider = _build_provider_registry().create_embedding(
        settings.embeddings.provider,
        settings.embeddings,
    )
    configured_identity = provider.identity()

    handle = open_arcade_database(
        ArcadeConnectionSettings(
            path=resolved_path,
            create_if_missing=False,
            embedded_single_process=settings.database.embedded_single_process,
        )
    )
    try:
        schema_version = max(read_schema_version(handle.database), 1)
        repository = ArcadeMemoryRepository(handle.database, schema_version=schema_version)
        records = (
            repository.list_matching_scope(scope)
            if scope is not None
            else repository.list_matching_scope(None)
        )
        candidates = _collect_reembed_candidates(records, configured_identity)
        if limit is not None:
            candidates = candidates[:limit]

        if dry_run or not candidates:
            return {
                "dry_run": dry_run,
                "database_path": str(resolved_path),
                "configured_identity": _serialize_provider_identity(configured_identity),
                "candidate_count": len(candidates),
                "updated_count": 0,
                "candidates": candidates,
            }

        batch_size = max(1, settings.embeddings.batch_size)
        updated_count = 0
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            batch_records = [item["record"] for item in batch]
            texts = [
                build_embedding_text(
                    record,
                    include_heading_path=settings.chunking.include_heading_path,
                    include_frontmatter=settings.chunking.include_frontmatter_in_embedding,
                )
                for record in batch_records
            ]
            embedded_batch = provider.embed_batch(texts)
            if len(embedded_batch) != len(batch_records):
                raise PersistenceError(
                    "Embedding provider returned a batch with an unexpected length "
                    "during re-embedding."
                )
            for record, embedded in zip(batch_records, embedded_batch, strict=False):
                repository.replace_memory(
                    record.model_copy(
                        update={
                            "embedding": embedded.vector,
                            "embedding_model": embedded.model,
                            "embedding_model_version": embedded.model_version,
                            "embedding_dim": embedded.dim,
                            "embedding_created_at": embedded.created_at,
                        }
                    )
                )
                updated_count += 1
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
        handle.close()

    return {
        "dry_run": False,
        "database_path": str(resolved_path),
        "configured_identity": _serialize_provider_identity(configured_identity),
        "candidate_count": len(candidates),
        "updated_count": updated_count,
        "candidates": [
            {key: value for key, value in item.items() if key != "record"}
            for item in candidates
        ],
    }


def _collect_database_snapshot(
    database_path: Path,
    settings: MemoryStoreSettings,
) -> dict[str, Any]:
    handle = open_arcade_database(
        ArcadeConnectionSettings(
            path=database_path,
            create_if_missing=False,
            embedded_single_process=settings.database.embedded_single_process,
        )
    )
    try:
        schema_version = read_schema_version(handle.database)
        repository = ArcadeMemoryRepository(handle.database, schema_version=max(schema_version, 1))
        active_dimension: int | None = None
        issues: list[str] = []
        try:
            active_dimension = read_active_index_dimension(handle.database)
        except Exception as exc:
            issues.append(str(exc))

        try:
            migration_plan = _serialize_migration_result(
                plan_database_migrations(
                    handle.database,
                    database_path=database_path,
                    expected_version=settings.database.schema_version,
                )
            )
        except Exception as exc:
            migration_plan = None
            issues.append(str(exc))

        return {
            "schema_version": schema_version,
            "active_embedding_dimension": active_dimension,
            "stats": repository.aggregate_stats(),
            "embedding_identities": _query_embedding_identities(handle.database),
            "graph_links": _query_edge_counts(handle.database),
            "migration_plan": migration_plan,
            "issues": issues,
        }
    finally:
        handle.close()


def _query_embedding_identities(database: Any) -> list[dict[str, Any]]:
    rows = database.query(
        "sql",
        (
            f"SELECT embedding_model as model_name, embedding_model_version as model_version, "
            f"embedding_dim as embedding_dim, count(*) as count FROM {MEMORY_RECORD_VERTEX} "
            "WHERE embedding IS NOT NULL "
            "GROUP BY embedding_model, embedding_model_version, embedding_dim"
        ),
    )
    identities: list[dict[str, Any]] = []
    for row in rows:
        identities.append(
            {
                "model_name": row.get("model_name"),
                "model_version": row.get("model_version"),
                "embedding_dim": row.get("embedding_dim"),
                "count": int(row.get("count") or 0),
            }
        )
    identities.sort(
        key=lambda item: (
            str(item.get("model_name") or ""),
            str(item.get("model_version") or ""),
            int(item.get("embedding_dim") or 0),
        )
    )
    return identities


def _query_edge_counts(database: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge_type in EDGE_TYPES:
        try:
            result = database.query("sql", f"SELECT count(*) as count FROM {edge_type}").first()
        except Exception:
            result = None
        counts[edge_type] = int(result.get("count") or 0) if result is not None else 0
    return counts


def _run_search_checks(
    database_path: Path,
    *,
    settings: MemoryStoreSettings,
    search_queries: Sequence[str],
    scope: Scope | None,
) -> list[dict[str, Any]]:
    if not search_queries:
        return []

    override_settings = settings.model_copy(
        update={
            "database": settings.database.model_copy(update={"path": database_path}),
        }
    )
    store = MemoryStore(settings=override_settings)
    checks: list[dict[str, Any]] = []
    try:
        for query in search_queries:
            results = store.search(
                MemorySearchQuery(
                    scope=scope or Scope(),
                    text=query,
                    limit=5,
                )
            )
            checks.append(
                {
                    "query": query,
                    "result_count": len(results),
                    "ok": len(results) > 0,
                }
            )
    finally:
        store.close()
    return checks


def _build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_embedding("fastembed", FastEmbedProvider.from_settings)
    registry.register_embedding("ollama", OllamaEmbeddingProvider.from_settings)
    registry.register_embedding("llamacpp", LlamaCppEmbeddingProvider.from_settings)
    return registry


def _collect_reembed_candidates(
    records: Sequence[MemoryRecord],
    configured_identity: Any,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for record in records:
        reason = _record_reembed_reason(record, configured_identity)
        if reason is None:
            continue
        candidates.append(
            {
                "memory_id": record.memory_id,
                "stable_key": record.stable_key,
                "reason": reason,
                "record": record,
            }
        )
    return candidates


def _record_reembed_reason(record: MemoryRecord, configured_identity: Any) -> str | None:
    if record.embedding is None:
        return "missing_embedding"
    if (
        configured_identity.vector_dimension is not None
        and record.embedding_dim != configured_identity.vector_dimension
    ):
        return "dimension_mismatch"
    if record.embedding_model != configured_identity.model_name:
        return "model_name_mismatch"
    expected_version = configured_identity.persistence_version
    if expected_version is not None and record.embedding_model_version != expected_version:
        return "model_version_mismatch"
    return None


def _configured_embedding_identity(settings: MemoryStoreSettings) -> dict[str, Any]:
    version = settings.embeddings.model_revision or settings.embeddings.model_version
    return {
        "provider": settings.embeddings.provider,
        "model_name": settings.embeddings.model,
        "model_version": version,
        "embedding_dim": settings.embeddings.dim,
    }


def _serialize_provider_identity(identity: Any) -> dict[str, Any]:
    return {
        "provider": identity.provider,
        "capability": identity.capability,
        "model_name": identity.model_name,
        "model_version": identity.persistence_version,
        "embedding_dim": identity.vector_dimension,
    }


def _serialize_migration_result(result: Any) -> dict[str, Any]:
    payload = asdict(result)
    for key in ("database_path", "backup_path"):
        if payload.get(key) is not None:
            payload[key] = str(payload[key])
    return payload


def _required_embedding_dimension(settings: MemoryStoreSettings) -> int:
    if settings.embeddings.dim is None:
        raise PersistenceError(
            "MemoryStoreSettings.embeddings.dim must be set for database migration operations."
        )
    return settings.embeddings.dim


def _ensure_runtime_available() -> None:
    if not arcade_runtime_available():
        raise PersistenceError("arcadedb_embedded is not installed in the current environment.")


def _deduplicate_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique