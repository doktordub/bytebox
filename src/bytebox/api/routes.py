"""REST routes that delegate to the shared service layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..auth import AuthScope
from ..config import MemoryStoreSettings
from ..errors import AuthorizationError, ValidationError
from ..errors import MemoryNotFoundError
from ..observability.diagnostics import (
    build_liveness_report,
    build_metrics_payload,
    build_readiness_report,
    build_state_report,
    build_status_report,
)
from ..models import (
    ChunkContextResponse,
    ChunkSearchQuery,
    ChunkSearchResult,
    FolderIngestResult,
    HealthLiveness,
    HealthReadiness,
    HealthStateReport,
    HealthStatus,
    HealthStatusReport,
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
from .security import (
    DELETE_CONFIRMATION_HEADER_NAME,
    DELETE_CONFIRMATION_VALUE,
    IDEMPOTENCY_KEY_HEADER_NAME,
)


def register_routes(
    app: Any,
    store: MemoryStore,
    *,
    authorizer: Any | None = None,
    settings: MemoryStoreSettings | None = None,
) -> None:
    def route_kwargs(*required_scopes: str) -> dict[str, Any]:
        dependencies = []
        if authorizer is not None:
            dependencies.extend(authorizer.dependencies_for(*required_scopes))
        return {"dependencies": dependencies}

    live_scopes: tuple[str, ...] = () if settings is not None and settings.api.health_live_anonymous else (AuthScope.ADMIN_READ.value,)
    ready_scopes: tuple[str, ...] = () if settings is not None and settings.api.health_ready_anonymous else (AuthScope.ADMIN_READ.value,)
    metrics_scopes: tuple[str, ...] = () if settings is not None and settings.api.metrics_anonymous else (AuthScope.ADMIN_READ.value,)

    @app.post("/memories", response_model=MemoryRecord, **route_kwargs(AuthScope.MEMORY_WRITE.value))
    def create_memory(payload: CreateMemoryRequest) -> MemoryRecord:
        return store.add_memory(payload.memory, embed=payload.embed)

    @app.get("/memories/{memory_id}", response_model=MemoryRecord, **route_kwargs(AuthScope.MEMORY_READ.value))
    def get_memory(memory_id: str) -> MemoryRecord:
        record = store.get_memory(memory_id)
        if record is None:
            raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")
        return record

    @app.patch("/memories/{memory_id}", response_model=MemoryRecord, **route_kwargs(AuthScope.MEMORY_WRITE.value))
    def update_memory(memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return store.update_memory(memory_id, patch)

    @app.post("/memories/search", response_model=list[MemorySearchResult], **route_kwargs(AuthScope.MEMORY_READ.value))
    def search_memories(query: MemorySearchQuery) -> list[MemorySearchResult]:
        return store.search(query)

    @app.post("/chunks/search", response_model=list[ChunkSearchResult], **route_kwargs(AuthScope.MEMORY_READ.value))
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

    @app.get("/chunks/{chunk_id}", response_model=ChunkSearchResult, **route_kwargs(AuthScope.MEMORY_READ.value))
    def get_chunk(chunk_id: str) -> ChunkSearchResult:
        record = store.get_chunk(chunk_id)
        if record is None:
            raise MemoryNotFoundError(f"Document chunk was not found: {chunk_id}")
        return record

    @app.get(
        "/chunks/{chunk_id}/context",
        response_model=ChunkContextResponse,
        **route_kwargs(AuthScope.MEMORY_READ.value),
    )
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

    @app.post(
        "/documents/ingest",
        response_model=IngestResult,
        **route_kwargs(AuthScope.MEMORY_INGEST.value),
    )
    def ingest_document(payload: IngestDocumentRequest) -> IngestResult:
        kwargs: dict[str, Any] = {}
        if payload.dry_run:
            kwargs["dry_run"] = True
        return store.ingest_document(payload.path, payload.scope, **kwargs)

    @app.post(
        "/documents/ingest-folder",
        response_model=FolderIngestResult,
        **route_kwargs(AuthScope.MEMORY_INGEST.value),
    )
    def ingest_folder(payload: IngestFolderRequest) -> FolderIngestResult:
        kwargs: dict[str, Any] = {
            "stop_on_error": payload.stop_on_error,
            "continue_on_error": payload.continue_on_error,
            "resume_from": payload.resume_from,
            "connection_strategy": payload.connection_strategy,
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

    @app.post(
        "/memories/{memory_id}/feedback",
        response_model=MemoryRecord,
        **route_kwargs(AuthScope.MEMORY_WRITE.value),
    )
    def add_feedback(memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return store.add_feedback(memory_id, feedback)

    @app.post(
        "/memories/{memory_id}/forget",
        response_model=MemoryRecord,
        **route_kwargs(AuthScope.MEMORY_DELETE.value),
    )
    def forget_memory(memory_id: str) -> MemoryRecord:
        store.forget(memory_id)
        record = store.get_memory(memory_id)
        if record is None:
            raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")
        return record

    @app.post(
        "/memories/export",
        response_model=MemoryExport,
        **route_kwargs(AuthScope.MEMORY_EXPORT.value),
    )
    def export_memories(payload: ExportMemoriesRequest) -> MemoryExport:
        if settings is not None and not settings.security.export_enabled:
            raise AuthorizationError("Memory export is disabled.")
        if payload.scope is None and payload.user_id is not None:
            return payload.to_export(store.export_user_memories(payload.user_id))
        return store.export_scope(payload.resolved_scope)

    @app.post(
        "/memories/import",
        response_model=ImportResult,
        **route_kwargs(AuthScope.MEMORY_IMPORT.value),
    )
    def import_memories(payload: ImportMemoriesRequest, request: Request) -> ImportResult:
        if settings is not None:
            if not settings.security.import_enabled:
                raise AuthorizationError("Memory import is disabled.")
            if (
                settings.security.require_import_idempotency_key
                and not request.headers.get(IDEMPOTENCY_KEY_HEADER_NAME)
            ):
                raise ValidationError(
                    f"{IDEMPOTENCY_KEY_HEADER_NAME} is required for import requests."
                )
        return store.import_memories(payload.payload, mode=payload.mode)

    @app.post(
        "/memories/delete-by-scope",
        response_model=DeleteByScopeResponse,
        **route_kwargs(AuthScope.MEMORY_DELETE.value),
    )
    def delete_by_scope(payload: DeleteByScopeRequest, request: Request) -> DeleteByScopeResponse:
        if payload.hard_delete:
            if authorizer is not None:
                authorizer.assert_request_scopes(request, AuthScope.ADMIN_OPERATE.value)
            if settings is not None and not settings.security.hard_delete_enabled:
                raise AuthorizationError("Hard delete operations are disabled.")
            confirmation = request.headers.get(DELETE_CONFIRMATION_HEADER_NAME, "")
            if confirmation.strip().lower() != DELETE_CONFIRMATION_VALUE:
                raise ValidationError(
                    f"{DELETE_CONFIRMATION_HEADER_NAME} must be set to '{DELETE_CONFIRMATION_VALUE}'."
                )
        deleted = store.delete_by_scope(payload.scope, hard_delete=payload.hard_delete)
        return DeleteByScopeResponse(deleted=deleted, hard_delete=payload.hard_delete)

    @app.get("/health/live", response_model=HealthLiveness, **route_kwargs(*live_scopes))
    def health_live() -> HealthLiveness:
        return build_liveness_report()

    @app.get("/health/ready", response_model=HealthReadiness, **route_kwargs(*ready_scopes))
    def health_ready(request: Request) -> Any:
        report = build_readiness_report(
            store=store,
            settings=settings or MemoryStoreSettings(),
            container=getattr(request.app.state, "bytebox_container", None),
        )
        status_code = 200 if report.status == "ready" else 503
        return JSONResponse(status_code=status_code, content=report.model_dump(mode="json"))

    @app.get("/health", response_model=HealthStatus, **route_kwargs(AuthScope.ADMIN_READ.value))
    def health() -> HealthStatus:
        return store.health()

    @app.get("/status", response_model=HealthStatusReport, **route_kwargs(AuthScope.ADMIN_READ.value))
    def status(request: Request) -> HealthStatusReport:
        return build_status_report(
            store=store,
            settings=settings or MemoryStoreSettings(),
            container=getattr(request.app.state, "bytebox_container", None),
        )

    @app.get("/state", response_model=HealthStateReport, **route_kwargs(AuthScope.ADMIN_OPERATE.value))
    def state(request: Request) -> HealthStateReport:
        return build_state_report(
            store=store,
            settings=settings or MemoryStoreSettings(),
            container=getattr(request.app.state, "bytebox_container", None),
            metrics=getattr(request.app.state, "bytebox_metrics", None),
        )

    @app.get("/metrics", response_class=PlainTextResponse, **route_kwargs(*metrics_scopes))
    def metrics(request: Request) -> PlainTextResponse:
        payload = build_metrics_payload(
            settings=settings or MemoryStoreSettings(),
            container=getattr(request.app.state, "bytebox_container", None),
            metrics=getattr(request.app.state, "bytebox_metrics", None),
        )
        return PlainTextResponse(payload, media_type="text/plain; version=0.0.4")

    @app.get("/stats", response_model=MemoryStats, **route_kwargs(AuthScope.ADMIN_READ.value))
    def stats() -> MemoryStats:
        return store.stats()
