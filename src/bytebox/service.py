"""Shared compatibility facade used by both direct Python calls and adapters."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from time import monotonic
from typing import Any, NoReturn

from .arcade import (
    ArcadeConnectionSettings,
    ArcadeMemoryRepository,
    open_arcade_database,
    run_database_migrations,
)
from .arcade.connection import ArcadeDatabaseHandle, normalize_database_path
from .arcade.transactions import run_in_transaction
from .config import MemoryStoreSettings, load_settings
from .embeddings import (
    EmbeddedText,
    FastEmbedProvider,
    FastEmbedRerankerProvider,
    LlamaCppEmbeddingProvider,
    LlamaCppRerankerProvider,
    OllamaEmbeddingProvider,
    OllamaLLMRerankerProvider,
    ProviderRegistry,
    SharedAsyncHttpClient,
    build_embedding_text,
    read_model_manifest,
    resolve_manifest_path,
    validate_active_index_dimension,
    validate_embedding_dimensions,
)
from .errors import (
    EmbeddingDimensionMismatchError,
    MemoryStoreError,
    PersistenceError,
    ProviderError,
)
from .lifecycle import MemoryLifecycleManager
from .models import (
    ChunkContextResponse,
    ChunkSearchResult,
    FolderIngestConnectionStrategy,
    FolderIngestResult,
    HealthStatus,
    ImportMode,
    ImportResult,
    IngestCounters,
    IngestDiagnostics,
    IngestPhase,
    IngestResult,
    IngestTimings,
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
from .privacy import PrivacyController
from .observability.logging import log_event, log_exception_event
from .services import AdministrationService, MemoryCommandService, MemoryQueryService
from .services.ingestion import DocumentIngestionService
from .services.lifecycle import LifecycleService
from .services.privacy import PrivacyService
from .services.retrieval import RetrievalService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService:
    """Compatibility facade over focused ByteBox application services."""

    def __init__(self, settings: MemoryStoreSettings | None = None) -> None:
        self.settings = settings or MemoryStoreSettings()
        self._database_handle: ArcadeDatabaseHandle | None = None
        self._repository_instance: ArcadeMemoryRepository | None = None
        self._embedding_provider_instance: Any | None = None
        self._reranker_provider_instance: Any | None = None
        self._lifecycle_manager_instance: MemoryLifecycleManager | None = None
        self._privacy_controller_instance: PrivacyController | None = None
        self._active_embedding_dimension: int | None = None
        self._schema_version = self.settings.database.schema_version
        self._shared_http_clients: dict[str, SharedAsyncHttpClient] = {}
        self._provider_registry = ProviderRegistry()
        self._provider_registry.register_embedding("fastembed", self._build_fastembed_embedding_provider)
        self._provider_registry.register_embedding("ollama", self._build_ollama_embedding_provider)
        self._provider_registry.register_embedding("llamacpp", self._build_llamacpp_embedding_provider)
        self._provider_registry.register_reranker("fastembed", self._build_fastembed_reranker_provider)
        self._provider_registry.register_reranker("llamacpp", self._build_llamacpp_reranker_provider)
        self._provider_registry.register_reranker("ollama_llm", self._build_ollama_llm_reranker_provider)

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        **overrides: Any,
    ) -> "MemoryService":
        return cls(load_settings(config_path, **overrides))

    def close(self) -> None:
        handle = self._database_handle
        embedding_provider = self._embedding_provider_instance
        reranker_provider = self._reranker_provider_instance
        shared_http_clients = list(self._shared_http_clients.values())
        self._database_handle = None
        self._repository_instance = None
        self._lifecycle_manager_instance = None
        self._privacy_controller_instance = None
        self._embedding_provider_instance = None
        self._reranker_provider_instance = None
        self._active_embedding_dimension = None
        self._shared_http_clients = {}
        self._close_resource(embedding_provider)
        self._close_resource(reranker_provider)
        for client in shared_http_clients:
            self._close_resource(client)
        if handle is not None:
            handle.close()

    def initialize(
        self,
        *,
        warmup_embedding_provider: bool = False,
        warmup_reranker_provider: bool = False,
    ) -> "MemoryService":
        self._ensure_repository()
        if warmup_embedding_provider:
            self._embedding_provider()
        if warmup_reranker_provider and self.settings.reranker.enabled:
            self._reranker_provider()
        return self

    def validate_model_providers(self) -> None:
        providers: list[Any] = []
        if self._provider_needs_startup_validation(self.settings.embeddings.provider):
            providers.append(self._embedding_provider())
        if (
            self.settings.reranker.enabled
            and self._provider_needs_startup_validation(self.settings.reranker.provider)
        ):
            reranker_provider = self._reranker_provider()
            if reranker_provider is not None:
                providers.append(reranker_provider)

        for provider in providers:
            validator = getattr(provider, "validate_startup", None)
            if callable(validator):
                validator()

    @property
    def is_initialized(self) -> bool:
        return self._database_handle is not None and self._repository_instance is not None

    @property
    def database_handle(self) -> ArcadeDatabaseHandle | None:
        return self._database_handle

    @property
    def repository(self) -> ArcadeMemoryRepository | None:
        return self._repository_instance

    @property
    def embedding_provider(self) -> Any | None:
        return self._embedding_provider_instance

    @property
    def reranker_provider(self) -> Any | None:
        return self._reranker_provider_instance

    @property
    def shared_http_clients(self) -> dict[str, SharedAsyncHttpClient]:
        return self._shared_http_clients

    def __enter__(self) -> "MemoryService":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()

    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord:
        return self._commands().add_memory(memory, embed=embed)

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return self._queries().get_memory(memory_id)

    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return self._commands().update_memory(memory_id, patch)

    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
        *,
        embed: bool = False,
    ) -> MemoryRecord:
        return self._commands().upsert_memory(memory, stable_key=stable_key, embed=embed)

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        started = monotonic()
        try:
            results = self._retrieval_service().search(query)
        except Exception as exc:
            log_exception_event(
                "retrieval.failed",
                exc,
                operation="memory.search",
                component="retrieval",
                outcome="failure",
                limit=query.limit,
                scope=str(query.scope),
            )
            raise

        log_event(
            "retrieval.completed",
            operation="memory.search",
            component="retrieval",
            outcome="success",
            result_count=len(results),
            limit=query.limit,
            duration_ms=round((monotonic() - started) * 1000.0, 3),
        )
        return results

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
        return self._retrieval_service().search_document_chunks(
            text=text,
            scope=scope,
            limit=limit,
            before=before,
            after=after,
            include_removed=include_removed,
            allow_retrieval_only=allow_retrieval_only,
        )

    def get_chunk(self, chunk_id: str, *, scope: Scope | None = None) -> ChunkSearchResult | None:
        return self._retrieval_service().get_chunk(chunk_id, scope=scope)

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        return self._retrieval_service().get_chunk_context(
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
        progress_callback: Any = None,
        progress_every_chunks: int = 0,
    ) -> IngestResult:
        started = monotonic()
        try:
            result = self._ingestion_service().ingest_document(
                path,
                scope,
                dry_run=dry_run,
                progress_callback=progress_callback,
                progress_every_chunks=progress_every_chunks,
            )
        except Exception as exc:
            log_exception_event(
                "ingestion.document.failed",
                exc,
                operation="document.ingest",
                component="ingestion",
                outcome="failure",
                dry_run=dry_run,
            )
            raise

        log_event(
            "ingestion.document.completed",
            operation="document.ingest",
            component="ingestion",
            outcome="success",
            dry_run=dry_run,
            added=result.added,
            duration_ms=round((monotonic() - started) * 1000.0, 3),
        )
        return result

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
        progress_callback: Any = None,
    ) -> FolderIngestResult:
        started = monotonic()
        try:
            result = self._ingestion_service().ingest_folder(
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
        except Exception as exc:
            log_exception_event(
                "ingestion.batch.failed",
                exc,
                operation="folder.ingest",
                component="ingestion",
                outcome="failure",
                dry_run=dry_run,
            )
            raise

        log_event(
            "ingestion.batch.completed",
            operation="folder.ingest",
            component="ingestion",
            outcome="success",
            dry_run=dry_run,
            files_processed=result.files_processed,
            failed_files=result.failed_files,
            duration_ms=round((monotonic() - started) * 1000.0, 3),
        )
        return result

    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
        return self._lifecycle_service().promote(memory_id, reason=reason)

    def supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
        reason: str | None = None,
    ) -> None:
        self._lifecycle_service().supersede(old_memory_id, new_memory_id, reason=reason)

    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None:
        self._lifecycle_service().contradict(memory_id_a, memory_id_b, reason=reason)

    def expire(self, memory_id: str, reason: str | None = None) -> None:
        self._lifecycle_service().expire(memory_id, reason=reason)

    def forget(self, memory_id: str) -> None:
        self._privacy_service().forget(memory_id)

    def forget_by_user(self, user_id: str) -> int:
        return self._privacy_service().forget_by_user(user_id)

    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
        return self._privacy_service().delete_by_scope(scope, hard_delete=hard_delete)

    def disable_memory(self, scope: Scope) -> int:
        return self._privacy_service().disable_memory(scope)

    def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
        return self._privacy_service().export_user_memories(user_id)

    def export_scope(self, scope: Scope) -> MemoryExport:
        return self._privacy_service().export_scope(scope)

    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult:
        return self._privacy_service().import_memories(payload, mode=mode)

    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult:
        return self._privacy_service().redact(patterns, scope=scope)

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return self._lifecycle_service().add_feedback(memory_id, feedback)

    def stats(self) -> MemoryStats:
        return self._administration().stats()

    def health(self) -> HealthStatus:
        return self._administration().health()

    def _commands(self) -> MemoryCommandService:
        return MemoryCommandService(self)

    def _queries(self) -> MemoryQueryService:
        return MemoryQueryService(self)

    def _retrieval_service(self) -> RetrievalService:
        return RetrievalService(self)

    def _ingestion_service(self) -> DocumentIngestionService:
        return DocumentIngestionService(self)

    def _lifecycle_service(self) -> LifecycleService:
        return LifecycleService(self)

    def _privacy_service(self) -> PrivacyService:
        return PrivacyService(self)

    def _administration(self) -> AdministrationService:
        return AdministrationService(self)

    def _utcnow(self) -> datetime:
        return _utcnow()

    def _run_in_transaction(self, database: Any, operation: Any) -> None:
        run_in_transaction(database, operation)

    def _annotate_ingestion_exception(
        self,
        exc: MemoryStoreError,
        *,
        path: Path,
        phase: IngestPhase,
        counters: IngestCounters,
        diagnostics: IngestDiagnostics | None = None,
        timings: IngestTimings,
        root_exc: Exception | None = None,
    ) -> MemoryStoreError:
        chain = self._format_exception_chain(root_exc or exc)
        if root_exc is not None:
            chain.insert(0, self._format_exception_summary(exc))

        resolved_diagnostics = diagnostics or IngestDiagnostics()

        setattr(exc, "document_path", Path(path))
        setattr(exc, "ingest_phase", phase.value)
        setattr(exc, "ingest_counters", counters.model_dump(mode="python"))
        setattr(exc, "ingest_diagnostics", resolved_diagnostics.model_dump(mode="python"))
        setattr(exc, "ingest_timings", timings.model_dump(mode="python"))
        setattr(exc, "exception_class", exc.__class__.__name__)
        setattr(exc, "exception_chain", chain)
        return exc

    def _build_failed_ingest_result(self, path: Path, exc: MemoryStoreError) -> IngestResult:
        counters = IngestCounters.model_validate(getattr(exc, "ingest_counters", {}))
        diagnostics = IngestDiagnostics.model_validate(getattr(exc, "ingest_diagnostics", {}))
        timings = IngestTimings.model_validate(getattr(exc, "ingest_timings", {}))
        phase_value = getattr(exc, "ingest_phase", IngestPhase.PERSIST.value)

        return IngestResult(
            path=path,
            ok=False,
            phase=IngestPhase(phase_value),
            exception_class=getattr(exc, "exception_class", exc.__class__.__name__),
            error=str(exc),
            exception_chain=list(
                getattr(exc, "exception_chain", self._format_exception_chain(exc))
            ),
            counters=counters,
            diagnostics=diagnostics,
            timings=timings,
        )

    def _should_recover_after_failure(
        self,
        strategy: FolderIngestConnectionStrategy,
        exc: MemoryStoreError,
    ) -> bool:
        if strategy is FolderIngestConnectionStrategy.REOPEN_PER_FILE:
            return True
        if strategy is FolderIngestConnectionStrategy.REOPEN_ON_FAILURE:
            return self._exception_chain_contains(exc, PersistenceError)
        return False

    def _verify_lock_released(self) -> None:
        if not self.settings.database.embedded_single_process:
            return

        database_path = normalize_database_path(self.settings.database.path)
        lock_path = database_path.parent / f"{database_path.name}.lock"
        if lock_path.exists():
            raise PersistenceError(f"ArcadeDB lock was not released after close: {lock_path}")

    def _format_exception_chain(self, exc: BaseException) -> list[str]:
        return [self._format_exception_summary(item) for item in self._iter_exception_chain(exc)]

    def _format_exception_summary(self, exc: BaseException) -> str:
        message = str(exc)
        if not message:
            return exc.__class__.__name__
        return f"{exc.__class__.__name__}: {message}"

    def _exception_chain_contains(
        self,
        exc: BaseException,
        expected_type: type[BaseException],
    ) -> bool:
        return any(isinstance(item, expected_type) for item in self._iter_exception_chain(exc))

    def _iter_exception_chain(self, exc: BaseException) -> list[BaseException]:
        chain: list[BaseException] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            chain.append(current)
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return chain

    def _repository(self) -> ArcadeMemoryRepository:
        self._ensure_repository()
        if self._repository_instance is None:
            raise PersistenceError("Repository is not available.")
        return self._repository_instance

    def _ensure_repository(self) -> None:
        if self._database_handle is not None and self._repository_instance is not None:
            return

        embedding_dimensions = self.settings.embeddings.dim
        if embedding_dimensions is None:
            raise PersistenceError(
                "MemoryStoreSettings.embeddings.dim must be set to initialize the database schema."
            )

        database_path = normalize_database_path(self.settings.database.path)
        database_exists_before_open = database_path.exists()
        handle = open_arcade_database(
            ArcadeConnectionSettings.from_database_settings(self.settings.database)
        )

        try:
            log_event(
                "database.migration.started",
                operation="database.migrate",
                component="database",
                outcome="starting",
                existing_database=database_exists_before_open,
                database=database_path.name,
            )
            migration_result = run_database_migrations(
                handle.database,
                database_path=handle.database_path,
                expected_version=self.settings.database.schema_version,
                embedding_dimensions=embedding_dimensions,
                dry_run=False,
                create_backup=database_exists_before_open,
            )
            self._schema_version = migration_result.schema_version
            self._active_embedding_dimension = validate_active_index_dimension(
                handle.database,
                embedding_dimensions,
            )
        except Exception as exc:
            log_exception_event(
                "database.migration.failed",
                exc,
                operation="database.migrate",
                component="database",
                outcome="failure",
                database=database_path.name,
            )
            with suppress(PersistenceError):
                handle.close()
            raise

        log_event(
            "database.migration.completed",
            operation="database.migrate",
            component="database",
            outcome="success",
            database=database_path.name,
            schema_version=self._schema_version,
        )

        self._database_handle = handle
        self._repository_instance = ArcadeMemoryRepository(
            handle.database,
            schema_version=self._schema_version,
        )
        self._lifecycle_manager_instance = None
        self._privacy_controller_instance = None
        log_event(
            "database.opened",
            operation="database.open",
            component="database",
            outcome="success",
            database=database_path.name,
            schema_version=self._schema_version,
        )

    def _lifecycle(self) -> MemoryLifecycleManager:
        if self._lifecycle_manager_instance is None:
            self._lifecycle_manager_instance = MemoryLifecycleManager(self._repository())
        return self._lifecycle_manager_instance

    def _privacy(self) -> PrivacyController:
        if self._privacy_controller_instance is None:
            self._privacy_controller_instance = PrivacyController(
                self._repository(),
                lifecycle=self._lifecycle(),
                settings=self.settings.privacy,
            )
        return self._privacy_controller_instance

    def _apply_service_defaults(self, memory: MemoryCreate) -> MemoryCreate:
        updates: dict[str, Any] = {}

        if "sensitivity" not in memory.model_fields_set:
            updates["sensitivity"] = self.settings.privacy.default_sensitivity
        if "allow_retrieval" not in memory.model_fields_set:
            updates["allow_retrieval"] = self.settings.privacy.allow_retrieval_default
        if "allow_llm_context" not in memory.model_fields_set:
            updates["allow_llm_context"] = self.settings.privacy.allow_llm_context_default
        if memory.embedding is not None and memory.embedding_dim is None:
            updates["embedding_dim"] = len(memory.embedding)

        return memory if not updates else memory.model_copy(update=updates)

    def _prepare_memory_for_write(self, memory: MemoryCreate, *, embed: bool) -> MemoryCreate:
        if embed:
            return self._embed_memory(memory)

        if memory.embedding is None:
            return memory

        actual_dim = memory.embedding_dim or len(memory.embedding)
        try:
            validate_embedding_dimensions(
                self._configured_embedding_dimension(),
                actual_dim,
                context="configured embedding model",
            )
            validate_embedding_dimensions(
                self._active_embedding_dimension_value(),
                actual_dim,
                context="active vector index",
            )
        except EmbeddingDimensionMismatchError as exc:
            return self._handle_embedding_mismatch(memory, reason=str(exc))

        if not self._embedding_identity_is_compatible(memory):
            return self._handle_embedding_mismatch(
                memory,
                reason=(
                    "Configured embedding provider identity does not match the supplied "
                    "embedding metadata."
                ),
            )

        if memory.embedding_created_at is None:
            return memory.model_copy(update={"embedding_created_at": _utcnow()})
        return memory

    def _embed_memory(self, memory: MemoryCreate) -> MemoryCreate:
        provider = self._embedding_provider()
        text = build_embedding_text(
            memory,
            include_heading_path=self.settings.chunking.include_heading_path,
            include_frontmatter=self.settings.chunking.include_frontmatter_in_embedding,
        )
        embedded = provider.embed_text(text)

        return self._apply_embedded_text(memory, embedded)

    def _apply_embedded_text(self, memory: MemoryCreate, embedded: EmbeddedText) -> MemoryCreate:
        return self._prepare_memory_for_write(
            memory.model_copy(
                update={
                    "embedding": embedded.vector,
                    "embedding_model": embedded.model,
                    "embedding_model_version": embedded.model_version,
                    "embedding_dim": embedded.dim,
                    "embedding_created_at": embedded.created_at,
                }
            ),
            embed=False,
        )

    def _handle_embedding_mismatch(self, memory: MemoryCreate, *, reason: str) -> MemoryCreate:
        policy = self.settings.embeddings.dimension_mismatch
        if policy == "error":
            raise EmbeddingDimensionMismatchError(reason)
        if policy == "quarantine":
            return self._quarantine_embedding(memory, reason=reason)
        if policy == "reembed":
            stripped = memory.model_copy(
                update={
                    "embedding": None,
                    "embedding_model": None,
                    "embedding_model_version": None,
                    "embedding_dim": None,
                    "embedding_created_at": None,
                }
            )
            return self._embed_memory(stripped)
        raise PersistenceError(f"Unsupported dimension mismatch policy: {policy}")

    def _quarantine_embedding(self, memory: MemoryCreate, *, reason: str) -> MemoryCreate:
        metadata = dict(memory.metadata)
        metadata["embedding_quarantined"] = True
        metadata["embedding_quarantine_reason"] = reason
        metadata["embedding_quarantine_original_dim"] = memory.embedding_dim or (
            len(memory.embedding) if memory.embedding is not None else None
        )
        if memory.embedding_model is not None:
            metadata["embedding_quarantine_original_model"] = memory.embedding_model
        if memory.embedding_model_version is not None:
            metadata["embedding_quarantine_original_model_version"] = memory.embedding_model_version

        return memory.model_copy(
            update={
                "embedding": None,
                "embedding_model": None,
                "embedding_model_version": None,
                "embedding_dim": None,
                "embedding_created_at": None,
                "metadata": metadata,
            }
        )

    def _embedding_provider(self) -> Any:
        if self._embedding_provider_instance is not None:
            return self._embedding_provider_instance

        self._embedding_provider_instance = self._provider_registry.create_embedding(
            self.settings.embeddings.provider,
            self.settings.embeddings,
        )
        identity = self._provider_identity(self._embedding_provider_instance)
        if identity is not None:
            log_event(
                "provider.initialized",
                operation="provider.initialize",
                component="embeddings",
                outcome="success",
                provider=identity.provider,
                capability=identity.capability,
                model_name=identity.model_name,
                revision=identity.revision,
                vector_dimension=identity.vector_dimension,
            )
        return self._embedding_provider_instance

    def _reranker_provider(self) -> Any | None:
        if not self.settings.reranker.enabled:
            return None
        if self._reranker_provider_instance is not None:
            return self._reranker_provider_instance

        self._reranker_provider_instance = self._provider_registry.create_reranker(
            self.settings.reranker.provider,
            self.settings.reranker,
        )
        identity = self._provider_identity(self._reranker_provider_instance)
        if identity is not None:
            log_event(
                "provider.initialized",
                operation="provider.initialize",
                component="reranker",
                outcome="success",
                provider=identity.provider,
                capability=identity.capability,
                model_name=identity.model_name,
                revision=identity.revision,
            )
        return self._reranker_provider_instance

    def _embed_search_query(self, text: str) -> list[float]:
        return self._embedding_provider().embed_text(text).vector

    def _scoring_weights(self) -> dict[str, float]:
        weights = self.settings.scoring.weights
        configured = weights.model_dump()
        if not self.settings.retrieval.graph_expansion_enabled:
            configured["graph"] = 0.0
        return configured

    def _configured_embedding_dimension(self) -> int:
        dimension = self.settings.embeddings.dim
        if dimension is None:
            raise PersistenceError("MemoryStoreSettings.embeddings.dim must be configured.")
        return dimension

    def _embedding_identity_is_compatible(self, memory: MemoryCreate) -> bool:
        if memory.embedding is None:
            return True

        configured_model, configured_version = self._configured_embedding_identity()
        if memory.embedding_model is not None and memory.embedding_model != configured_model:
            return False
        if memory.embedding_model_version is None:
            return True
        if configured_version is None:
            return True
        return memory.embedding_model_version == configured_version

    def _configured_embedding_identity(self) -> tuple[str, str | None]:
        model_name = self.settings.embeddings.model
        model_version = self.settings.embeddings.model_revision or self.settings.embeddings.model_version
        manifest_path = resolve_manifest_path(
            self.settings.embeddings.model_path,
            self.settings.embeddings.manifest_path,
        )
        if manifest_path is not None and manifest_path.exists():
            try:
                manifest = read_model_manifest(manifest_path)
            except ProviderError:
                return model_name, model_version
            model_name = manifest.model_name
            model_version = manifest.revision or manifest.digest or model_version
        return model_name, model_version

    def _build_fastembed_embedding_provider(self, settings: Any) -> FastEmbedProvider:
        return self._instantiate_provider(
            FastEmbedProvider,
            settings,
            {
                "model": settings.model,
                "model_version": getattr(settings, "model_version", None),
                "model_path": getattr(settings, "model_path", None),
                "cache_dir": getattr(settings, "cache_dir", None),
                "local_files_only": getattr(settings, "local_files_only", False),
                "hf_hub_offline": getattr(settings, "hf_hub_offline", False),
                "threads": getattr(settings, "threads", None),
                "execution_providers": tuple(getattr(settings, "execution_providers", ()) or ()),
                "expected_dim": getattr(settings, "dim", None),
                "normalize": getattr(settings, "normalize", True),
                "model_revision": getattr(settings, "model_revision", None),
                "model_digest": getattr(settings, "model_digest", None),
                "manifest_path": getattr(settings, "manifest_path", None),
                "require_manifest": getattr(settings, "require_manifest", False),
                "require_checksums": getattr(settings, "require_checksums", False),
                "batch_size": getattr(settings, "batch_size", 64),
            },
        )

    def _build_ollama_embedding_provider(self, settings: Any) -> Any:
        return self._instantiate_provider(
            OllamaEmbeddingProvider,
            settings,
            {
                "shared_client": self._shared_http_client(settings),
            },
        )

    def _build_llamacpp_embedding_provider(self, settings: Any) -> Any:
        return self._instantiate_provider(
            LlamaCppEmbeddingProvider,
            settings,
            {
                "shared_client": self._shared_http_client(settings),
            },
        )

    def _build_fastembed_reranker_provider(self, settings: Any) -> Any:
        return self._instantiate_provider(
            FastEmbedRerankerProvider,
            settings,
            {
                "model": settings.model,
                "model_version": getattr(settings, "model_version", None),
                "model_path": getattr(settings, "model_path", None),
                "cache_dir": getattr(settings, "cache_dir", None),
                "local_files_only": getattr(settings, "local_files_only", False),
                "hf_hub_offline": getattr(settings, "hf_hub_offline", False),
                "threads": getattr(settings, "threads", None),
                "execution_providers": tuple(getattr(settings, "execution_providers", ()) or ()),
                "batch_size": getattr(settings, "batch_size", 32),
                "model_revision": getattr(settings, "model_revision", None),
                "model_digest": getattr(settings, "model_digest", None),
                "manifest_path": getattr(settings, "manifest_path", None),
                "require_manifest": getattr(settings, "require_manifest", False),
                "require_checksums": getattr(settings, "require_checksums", False),
            },
        )

    def _build_llamacpp_reranker_provider(self, settings: Any) -> Any:
        return self._instantiate_provider(
            LlamaCppRerankerProvider,
            settings,
            {
                "shared_client": self._shared_http_client(settings),
            },
        )

    def _build_ollama_llm_reranker_provider(self, settings: Any) -> Any:
        return self._instantiate_provider(
            OllamaLLMRerankerProvider,
            settings,
            {
                "shared_client": self._shared_http_client(settings),
            },
        )

    def _instantiate_provider(self, provider_cls: type[Any], settings: Any, kwargs: dict[str, Any]) -> Any:
        builder = getattr(provider_cls, "from_settings", None)
        if callable(builder):
            return self._call_provider_builder(builder, settings, kwargs)

        try:
            signature = inspect.signature(provider_cls)
        except (TypeError, ValueError):
            return provider_cls(**kwargs)

        if any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            return provider_cls(**kwargs)

        supported_kwargs = {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
        return provider_cls(**supported_kwargs)

    def _call_provider_builder(self, builder: Any, settings: Any, kwargs: dict[str, Any]) -> Any:
        try:
            signature = inspect.signature(builder)
        except (TypeError, ValueError):
            return builder(settings)

        if any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            return builder(settings, **kwargs)

        supported_kwargs = {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
        return builder(settings, **supported_kwargs)

    def _shared_http_client(self, settings: Any) -> SharedAsyncHttpClient | None:
        remote_settings = getattr(settings, "remote", None)
        if remote_settings is None or getattr(remote_settings, "base_url", None) is None:
            return None

        cache_key = json.dumps(remote_settings.model_dump(mode="json"), sort_keys=True)
        client = self._shared_http_clients.get(cache_key)
        if client is None:
            client = SharedAsyncHttpClient(
                name=f"{getattr(settings, 'provider', 'remote')}:{getattr(settings, 'model', 'remote')}",
                settings=remote_settings,
            )
            self._shared_http_clients[cache_key] = client
        return client

    @staticmethod
    def _provider_needs_startup_validation(provider_name: str) -> bool:
        return provider_name.lower() in {"llamacpp", "ollama", "ollama_llm"}

    @staticmethod
    def _provider_identity(provider: Any) -> Any | None:
        identity = getattr(provider, "identity", None)
        if not callable(identity):
            return None
        return identity()

    @staticmethod
    def _close_resource(resource: Any) -> None:
        closer = getattr(resource, "close", None)
        if callable(closer):
            with suppress(Exception):
                closer()

    def _active_embedding_dimension_value(self) -> int:
        self._ensure_repository()
        if self._active_embedding_dimension is not None:
            return self._active_embedding_dimension

        handle = self._database_handle
        if handle is None:
            raise PersistenceError("Database handle is not available.")

        self._active_embedding_dimension = validate_active_index_dimension(
            handle.database,
            self._configured_embedding_dimension(),
        )
        return self._active_embedding_dimension

    def _later_phase_only(self, capability: str) -> NoReturn:
        raise MemoryStoreError(
            f"{capability} is not implemented yet. "
            "Currently available: lifecycle controls, deterministic markdown ingestion, "
            "embeddings safety, CRUD, health, and stats. "
        )