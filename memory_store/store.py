"""Public Python facade for the memory store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import MemoryStoreSettings
from .models import (
    ChunkContextResponse,
    ChunkSearchResult,
    FolderIngestConnectionStrategy,
    FolderIngestResult,
    HealthStatus,
    ImportMode,
    ImportResult,
    IngestResult,
    MemoryCreate,
    MemoryExport,
    MemoryFeedback,
    MemoryImport,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStats,
    MemoryUpdate,
    RedactionResult,
    Scope,
)
from .service import MemoryService


class MemoryStore:
    """Thin public facade over the shared service layer."""

    def __init__(
        self,
        settings: MemoryStoreSettings | None = None,
        service: MemoryService | None = None,
    ) -> None:
        self._service = service or MemoryService(settings)

    def close(self) -> None:
        self._service.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()

    @classmethod
    def from_config(cls, config_path: str | Path | None = None, **overrides: Any) -> "MemoryStore":
        return cls(service=MemoryService.from_config(config_path, **overrides))

    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord:
        return self._service.add_memory(memory, embed=embed)

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return self._service.get_memory(memory_id)

    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return self._service.update_memory(memory_id, patch)

    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
        *,
        embed: bool = False,
    ) -> MemoryRecord:
        return self._service.upsert_memory(memory, stable_key=stable_key, embed=embed)

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        return self._service.search(query)

    def search_document_chunks(
        self,
        *,
        text: str,
        scope: Scope,
        limit: int = 10,
        before: int = 0,
        after: int = 0,
        include_removed: bool = False,
        allow_retrieval_only: bool = True,
    ) -> list[ChunkSearchResult]:
        return self._service.search_document_chunks(
            text=text,
            scope=scope,
            limit=limit,
            before=before,
            after=after,
            include_removed=include_removed,
            allow_retrieval_only=allow_retrieval_only,
        )

    def get_chunk(self, chunk_id: str, *, scope: Scope | None = None) -> ChunkSearchResult | None:
        return self._service.get_chunk(chunk_id, scope=scope)

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        return self._service.get_chunk_context(
            chunk_id,
            scope=scope,
            before=before,
            after=after,
        )

    def ingest_document(
        self,
        path: str | Path,
        scope: Scope,
        *,
        dry_run: bool = False,
    ) -> IngestResult:
        return self._service.ingest_document(path, scope, dry_run=dry_run)

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
        since: Any = None,
        progress_every_documents: int = 0,
        progress_every_chunks: int = 0,
        progress_callback: Any = None,
    ) -> FolderIngestResult:
        return self._service.ingest_folder(
            path,
            scope,
            stop_on_error=stop_on_error,
            continue_on_error=continue_on_error,
            resume_from=resume_from,
            connection_strategy=connection_strategy,
            dry_run=dry_run,
            manifest_path=manifest_path,
            only_failed=only_failed,
            limit=limit,
            since=since,
            progress_every_documents=progress_every_documents,
            progress_every_chunks=progress_every_chunks,
            progress_callback=progress_callback,
        )

    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
        return self._service.promote(memory_id, reason=reason)

    def supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
        reason: str | None = None,
    ) -> None:
        self._service.supersede(old_memory_id, new_memory_id, reason=reason)

    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None:
        self._service.contradict(memory_id_a, memory_id_b, reason=reason)

    def expire(self, memory_id: str, reason: str | None = None) -> None:
        self._service.expire(memory_id, reason=reason)

    def forget(self, memory_id: str) -> None:
        self._service.forget(memory_id)

    def forget_by_user(self, user_id: str) -> int:
        return self._service.forget_by_user(user_id)

    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
        return self._service.delete_by_scope(scope, hard_delete=hard_delete)

    def disable_memory(self, scope: Scope) -> int:
        return self._service.disable_memory(scope)

    def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
        return self._service.export_user_memories(user_id)

    def export_scope(self, scope: Scope) -> MemoryExport:
        return self._service.export_scope(scope)

    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult:
        return self._service.import_memories(payload, mode=mode)

    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult:
        return self._service.redact(patterns, scope=scope)

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return self._service.add_feedback(memory_id, feedback)

    def stats(self) -> MemoryStats:
        return self._service.stats()

    def health(self) -> HealthStatus:
        return self._service.health()
