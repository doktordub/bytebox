"""REST routes that delegate to the shared service layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import MemoryNotFoundError
from ..models import (
    ChunkContextResponse,
    ChunkSearchQuery,
    ChunkSearchResult,
    FolderIngestResult,
    HealthStatus,
    ImportResult,
    IngestResult,
    MemoryExport,
    MemoryFeedback,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStats,
    MemoryUpdate,
    Scope,
)
from ..store import MemoryStore
from .schemas import (
    CreateMemoryRequest,
    DeleteByScopeRequest,
    DeleteByScopeResponse,
    ExportMemoriesRequest,
    ImportMemoriesRequest,
    IngestDocumentRequest,
    IngestFolderRequest,
)


def register_routes(
    app: Any,
    store: MemoryStore,
    *,
    dependencies: Sequence[Any] | None = None,
) -> None:
    route_dependencies = list(dependencies or [])
    route_kwargs = {"dependencies": route_dependencies}

    @app.post("/memories", response_model=MemoryRecord, **route_kwargs)
    def create_memory(payload: CreateMemoryRequest) -> MemoryRecord:
        return store.add_memory(payload.memory, embed=payload.embed)

    @app.get("/memories/{memory_id}", response_model=MemoryRecord, **route_kwargs)
    def get_memory(memory_id: str) -> MemoryRecord:
        record = store.get_memory(memory_id)
        if record is None:
            raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")
        return record

    @app.patch("/memories/{memory_id}", response_model=MemoryRecord, **route_kwargs)
    def update_memory(memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return store.update_memory(memory_id, patch)

    @app.post("/memories/search", response_model=list[MemorySearchResult], **route_kwargs)
    def search_memories(query: MemorySearchQuery) -> list[MemorySearchResult]:
        return store.search(query)

    @app.post("/chunks/search", response_model=list[ChunkSearchResult], **route_kwargs)
    def search_document_chunks(query: ChunkSearchQuery) -> list[ChunkSearchResult]:
        return store.search_document_chunks(
            text=query.text,
            scope=query.scope,
            limit=query.limit,
            before=query.before,
            after=query.after,
            include_removed=query.include_removed,
            allow_retrieval_only=query.allow_retrieval_only,
        )

    @app.get("/chunks/{chunk_id}", response_model=ChunkSearchResult, **route_kwargs)
    def get_chunk(chunk_id: str) -> ChunkSearchResult:
        record = store.get_chunk(chunk_id)
        if record is None:
            raise MemoryNotFoundError(f"Document chunk was not found: {chunk_id}")
        return record

    @app.get("/chunks/{chunk_id}/context", response_model=ChunkContextResponse, **route_kwargs)
    def get_chunk_context(
        chunk_id: str,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        scope = None
        if any(value is not None for value in (user_id, project_id, agent_id)):
            scope = Scope(user_id=user_id, project_id=project_id, agent_id=agent_id)
        return store.get_chunk_context(
            chunk_id,
            scope=scope,
            before=before,
            after=after,
        )

    @app.post("/documents/ingest", response_model=IngestResult, **route_kwargs)
    def ingest_document(payload: IngestDocumentRequest) -> IngestResult:
        kwargs: dict[str, Any] = {}
        if payload.dry_run:
            kwargs["dry_run"] = True
        return store.ingest_document(payload.path, payload.scope, **kwargs)

    @app.post("/documents/ingest-folder", response_model=FolderIngestResult, **route_kwargs)
    def ingest_folder(payload: IngestFolderRequest) -> FolderIngestResult:
        kwargs: dict[str, Any] = {
            "stop_on_error": payload.stop_on_error,
            "continue_on_error": payload.continue_on_error,
            "resume_from": payload.resume_from,
            "connection_strategy": payload.connection_strategy,
            "manifest_path": payload.manifest_path,
            "only_failed": payload.only_failed,
            "limit": payload.limit,
            "since": payload.since,
        }
        if payload.dry_run:
            kwargs["dry_run"] = True
        return store.ingest_folder(
            payload.path,
            payload.scope,
            **kwargs,
        )

    @app.post("/memories/{memory_id}/feedback", response_model=MemoryRecord, **route_kwargs)
    def add_feedback(memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return store.add_feedback(memory_id, feedback)

    @app.post("/memories/{memory_id}/forget", response_model=MemoryRecord, **route_kwargs)
    def forget_memory(memory_id: str) -> MemoryRecord:
        store.forget(memory_id)
        record = store.get_memory(memory_id)
        if record is None:
            raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")
        return record

    @app.post("/memories/export", response_model=MemoryExport, **route_kwargs)
    def export_memories(payload: ExportMemoriesRequest) -> MemoryExport:
        if payload.scope is None and payload.user_id is not None:
            return payload.to_export(store.export_user_memories(payload.user_id))
        return store.export_scope(payload.resolved_scope)

    @app.post("/memories/import", response_model=ImportResult, **route_kwargs)
    def import_memories(payload: ImportMemoriesRequest) -> ImportResult:
        return store.import_memories(payload.payload, mode=payload.mode)

    @app.post("/memories/delete-by-scope", response_model=DeleteByScopeResponse, **route_kwargs)
    def delete_by_scope(payload: DeleteByScopeRequest) -> DeleteByScopeResponse:
        deleted = store.delete_by_scope(payload.scope, hard_delete=payload.hard_delete)
        return DeleteByScopeResponse(deleted=deleted, hard_delete=payload.hard_delete)

    @app.get("/health", response_model=HealthStatus, **route_kwargs)
    def health() -> HealthStatus:
        return store.health()

    @app.get("/stats", response_model=MemoryStats, **route_kwargs)
    def stats() -> MemoryStats:
        return store.stats()
