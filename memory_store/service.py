"""Shared service layer used by both direct Python calls and adapters."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, NoReturn

from .arcade import (
    ArcadeConnectionSettings,
    ArcadeMemoryRepository,
    arcade_runtime_available,
    ensure_database_schema,
    open_arcade_database,
    read_schema_version,
)
from .arcade.connection import ArcadeDatabaseHandle, normalize_database_path
from .arcade.transactions import run_in_transaction
from .config import MemoryStoreSettings, load_settings
from .embeddings import (
    EmbeddedText,
    FastEmbedProvider,
    build_embedding_text,
    fastembed_runtime_available,
    validate_active_index_dimension,
    validate_embedding_dimensions,
)
from .errors import (
    EmbeddingDimensionMismatchError,
    IngestionError,
    MemoryNotFoundError,
    MemoryStoreError,
    PersistenceError,
    RetrievalError,
)
from .ingestion import (
    chunk_markdown_sections,
    compute_chunk_id,
    compute_content_hash,
    compute_source_hash,
    normalize_source_path,
    read_markdown_file,
)
from .lifecycle import MemoryLifecycleManager
from .models import (
    ChunkContextResponse,
    ChunkSearchResult,
    FolderIngestConnectionStrategy,
    FolderIngestManifest,
    FolderIngestManifestEntry,
    FolderIngestManifestStatus,
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
    MemoryStatus,
    MemoryType,
    MemoryUpdate,
    RedactionResult,
    Scope,
    SourceType,
)
from .privacy import PrivacyController
from .retrieval import (
    build_hard_filter,
    deduplicate_candidates,
    expand_one_hop_candidates,
    filter_records,
    normalize_query,
    reciprocal_rank_fuse,
    rerank_candidates,
    search_full_text,
    search_vectors,
)
from .scoring import enrich_candidate_scores, normalize_candidate_scores, score_candidates


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started_at: float) -> int:
    return int(round((perf_counter() - started_at) * 1000))


class MemoryService:
    """Service layer with a shared ArcadeDB-backed repository."""

    def __init__(self, settings: MemoryStoreSettings | None = None) -> None:
        self.settings = settings or MemoryStoreSettings()
        self._database_handle: ArcadeDatabaseHandle | None = None
        self._repository_instance: ArcadeMemoryRepository | None = None
        self._embedding_provider_instance: FastEmbedProvider | None = None
        self._lifecycle_manager_instance: MemoryLifecycleManager | None = None
        self._privacy_controller_instance: PrivacyController | None = None
        self._active_embedding_dimension: int | None = None
        self._schema_version = self.settings.database.schema_version
        self._ensure_repository()

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        **overrides: Any,
    ) -> "MemoryService":
        return cls(load_settings(config_path, **overrides))

    def close(self) -> None:
        handle = self._database_handle
        self._database_handle = None
        self._repository_instance = None
        self._lifecycle_manager_instance = None
        self._privacy_controller_instance = None
        self._embedding_provider_instance = None
        self._active_embedding_dimension = None
        if handle is not None:
            handle.close()

    def __enter__(self) -> "MemoryService":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()

    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord:
        prepared = self._prepare_memory_for_write(
            self._apply_service_defaults(memory),
            embed=embed,
        )
        return self._repository().insert_memory(prepared)

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return self._repository().get_memory(memory_id)

    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return self._repository().update_memory(memory_id, patch)

    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
        *,
        embed: bool = False,
    ) -> MemoryRecord:
        return self._repository().upsert_memory(
            self._prepare_memory_for_write(self._apply_service_defaults(memory), embed=embed),
            stable_key=stable_key,
        )

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        repository = self._repository()
        try:
            normalized_query = normalize_query(query)
            hard_filter = build_hard_filter(query)
            records = filter_records(repository.list_by_scope(query.scope), query)
            if not records:
                return []

            vector_matches = search_vectors(
                records,
                normalized_query,
                top_n=self.settings.retrieval.vector_top_n,
                embed_query=self._embed_search_query,
            )
            full_text_matches = search_full_text(
                records,
                normalized_query,
                top_n=self.settings.retrieval.fts_top_n,
            )

            candidates = reciprocal_rank_fuse(
                vector_matches=vector_matches,
                full_text_matches=full_text_matches,
                rrf_k=self.settings.retrieval.rrf_k,
            )
            if not candidates:
                return []

            candidates = deduplicate_candidates(candidates)

            if (
                self.settings.retrieval.graph_expansion_enabled
                and self.settings.retrieval.graph_expansion_hops > 0
            ):
                candidates = expand_one_hop_candidates(
                    candidates,
                    hard_filter=hard_filter,
                    read_links=lambda memory_id: repository.read_one_hop_links(memory_id),
                )
                candidates = deduplicate_candidates(candidates)

            enrich_candidate_scores(
                candidates,
                temporal_settings=self.settings.scoring.temporal,
                now=_utcnow(),
            )
            weight_map = self._scoring_weights()
            normalize_candidate_scores(candidates)
            score_candidates(
                candidates,
                weight_map,
                reranker_enabled=self.settings.reranker.enabled,
            )
            candidates.sort(
                key=lambda candidate: (
                    -candidate.final_score,
                    -candidate.component_scores.get("retrieval_fusion", 0.0),
                    candidate.memory.memory_id,
                )
            )

            rerank_candidates(
                candidates,
                query_text=normalized_query.text,
                enabled=self.settings.reranker.enabled,
                model=self.settings.reranker.model,
                top_n=min(self.settings.reranker.top_n, len(candidates)),
            )

            normalize_candidate_scores(candidates)
            score_candidates(
                candidates,
                weight_map,
                reranker_enabled=self.settings.reranker.enabled,
            )
            candidates.sort(
                key=lambda candidate: (
                    -candidate.final_score,
                    -candidate.component_scores.get("retrieval_fusion", 0.0),
                    candidate.memory.memory_id,
                )
            )

            limit = min(query.limit, self.settings.retrieval.final_top_k)
            return [
                candidate.to_result(
                    include_component_scores=self.settings.retrieval.include_component_scores,
                    include_debug=self.settings.retrieval.include_debug,
                )
                for candidate in candidates[:limit]
            ]
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Search failed: {exc}") from exc

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
        results = self.search(
            MemorySearchQuery(
                scope=scope,
                text=text,
                limit=limit,
                memory_types=[MemoryType.DOCUMENT_CHUNK],
                include_removed=include_removed,
                allow_retrieval_only=allow_retrieval_only,
            )
        )
        return [ChunkSearchResult.from_search_result(result) for result in results]

    def get_chunk(self, chunk_id: str, *, scope: Scope | None = None) -> ChunkSearchResult | None:
        record = self._repository().get_chunk_by_id(chunk_id, scope=scope)
        if record is None:
            return None
        return ChunkSearchResult.from_record(record, debug={"lookup": "chunk_id"})

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        record = self._repository().get_chunk_by_id(chunk_id, scope=scope)
        if record is None:
            raise MemoryNotFoundError(f"Document chunk was not found: {chunk_id}")
        if record.source_path is None or record.document_chunk_index is None:
            raise RetrievalError(
                "Document chunk context requires source_path and document_chunk_index."
            )

        resolved_scope = scope or record.scope
        window = self._repository().list_chunk_window(
            record.source_path,
            scope=resolved_scope,
            document_chunk_index=record.document_chunk_index,
            before=before,
            after=after,
        )

        target = ChunkSearchResult.from_record(record, debug={"lookup": "chunk_context"})
        before_chunks: list[ChunkSearchResult] = []
        after_chunks: list[ChunkSearchResult] = []

        for window_record in window:
            chunk = ChunkSearchResult.from_record(
                window_record,
                debug={"lookup": "chunk_context"},
            )
            if window_record.memory_id == record.memory_id:
                target = chunk
                continue
            if (window_record.document_chunk_index or -1) < record.document_chunk_index:
                before_chunks.append(chunk)
                continue
            after_chunks.append(chunk)

        return ChunkContextResponse(
            chunk=target,
            before=before_chunks,
            after=after_chunks,
        )

    def ingest_document(
        self,
        path: str | Path,
        scope: Scope,
        *,
        dry_run: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_every_chunks: int = 0,
    ) -> IngestResult:
        document_path = Path(path)
        started_at = perf_counter()
        phase = IngestPhase.PARSE
        timings = IngestTimings()
        counters = IngestCounters()
        diagnostics = IngestDiagnostics(dry_run=dry_run)
        inserts: list[MemoryCreate] = []
        replacements: list[tuple[MemoryRecord, MemoryCreate]] = []
        removals: list[MemoryRecord] = []
        unchanged = 0

        try:
            parse_started_at = perf_counter()
            diagnostics.file_size_bytes = self._measure_file_size_bytes(document_path)
            diagnostics.limit_violations = self._collect_ingestion_limit_violations(
                file_size_bytes=diagnostics.file_size_bytes,
                frontmatter_bytes=0,
                section_count=0,
                chunk_count=0,
            )
            if diagnostics.limit_violations:
                raise IngestionError(
                    "Document ingest preflight limits exceeded: "
                    + "; ".join(diagnostics.limit_violations)
                )

            parsed = read_markdown_file(document_path)
            source_path = normalize_source_path(document_path)
            if diagnostics.file_size_bytes == 0:
                diagnostics.file_size_bytes = len(parsed.raw_text.encode("utf-8"))
            diagnostics.frontmatter_bytes = self._measure_frontmatter_bytes(parsed.raw_text)
            diagnostics.frontmatter_keys = [str(key) for key in parsed.frontmatter.keys()]
            sanitized_frontmatter, dropped_metadata_fields = self._sanitize_frontmatter_metadata(
                parsed.frontmatter
            )
            diagnostics.dropped_metadata_fields = dropped_metadata_fields
            timings.parse_ms = _elapsed_ms(parse_started_at)
            counters.section_count = len(parsed.sections)

            phase = IngestPhase.CHUNK
            chunk_started_at = perf_counter()
            chunks = chunk_markdown_sections(
                parsed.sections,
                strategy=self.settings.chunking.strategy,
                max_tokens=self.settings.chunking.max_tokens,
                overlap_tokens=self.settings.chunking.overlap_tokens,
                include_heading_path=self.settings.chunking.include_heading_path,
                preserve_code_blocks=self.settings.chunking.preserve_code_blocks,
            )
            timings.chunk_ms = _elapsed_ms(chunk_started_at)
            counters.chunk_count = len(chunks)

            diagnostics.limit_violations = self._collect_ingestion_limit_violations(
                file_size_bytes=diagnostics.file_size_bytes,
                frontmatter_bytes=diagnostics.frontmatter_bytes,
                section_count=counters.section_count,
                chunk_count=counters.chunk_count,
            )
            if diagnostics.limit_violations:
                raise IngestionError(
                    "Document ingest preflight limits exceeded: "
                    + "; ".join(diagnostics.limit_violations)
                )

            repository = self._repository()
            existing_chunks = repository.list_by_source_path(
                source_path,
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
            )
            existing_by_source_hash = {
                record.source_hash: record
                for record in existing_chunks
                if record.source_hash is not None
            }

            pending_writes: list[tuple[MemoryRecord | None, MemoryCreate]] = []
            planned_source_hashes: set[str] = set()
            for document_chunk_index, chunk in enumerate(chunks):
                source_hash = compute_source_hash(source_path, chunk.heading_path, chunk.chunk_index)
                planned_source_hashes.add(source_hash)

                candidate = self._build_document_chunk_memory(
                    scope=scope,
                    source_path=source_path,
                    parsed_document=parsed,
                    frontmatter=sanitized_frontmatter,
                    heading_path=chunk.heading_path,
                    section_index=chunk.section_index,
                    chunk_index=chunk.chunk_index,
                    document_chunk_index=document_chunk_index,
                    text=chunk.text,
                    approximate_tokens=chunk.approximate_token_count,
                    source_hash=source_hash,
                    dropped_frontmatter_fields=dropped_metadata_fields,
                )

                existing = existing_by_source_hash.get(source_hash)
                content_hash = candidate.metadata["content_hash"]
                if (
                    existing is not None
                    and existing.chunk_id == candidate.chunk_id
                    and existing.metadata.get("content_hash") == content_hash
                    and existing.status == MemoryStatus.ACTIVE
                    and existing.allow_retrieval
                    and existing.allow_llm_context
                ):
                    unchanged += 1
                    continue

                pending_writes.append((existing, candidate))

            removals = [
                record
                for source_hash, record in existing_by_source_hash.items()
                if source_hash not in planned_source_hashes
                and record.status not in {MemoryStatus.REMOVED, MemoryStatus.DELETED}
            ]
            counters.insert_count = sum(1 for existing, _memory in pending_writes if existing is None)
            counters.update_count = len(pending_writes) - counters.insert_count
            counters.remove_count = len(removals)

            if dry_run:
                timings.elapsed_ms = _elapsed_ms(started_at)
                return IngestResult(
                    path=document_path,
                    ok=True,
                    phase=IngestPhase.COMPLETE,
                    added=counters.insert_count,
                    updated=counters.update_count,
                    removed=counters.remove_count,
                    unchanged=unchanged,
                    counters=counters,
                    diagnostics=diagnostics,
                    timings=timings,
                )

            phase = IngestPhase.EMBED
            embed_started_at = perf_counter()
            prepared_writes = self._embed_document_candidates(
                pending_writes,
                path=document_path,
                total_document_chunks=counters.chunk_count,
                progress_callback=progress_callback,
                progress_every_chunks=progress_every_chunks,
            )
            timings.embed_ms += _elapsed_ms(embed_started_at)
            phase = IngestPhase.CHUNK

            for existing, prepared in prepared_writes:
                if existing is None:
                    inserts.append(prepared)
                else:
                    replacements.append((existing, prepared))

            phase = IngestPhase.PERSIST
            persist_started_at = perf_counter()
            removed = self._persist_document_ingestion(
                inserts=inserts,
                replacements=replacements,
                removals=removals,
            )
            timings.persist_ms = _elapsed_ms(persist_started_at)
            timings.elapsed_ms = _elapsed_ms(started_at)

            return IngestResult(
                path=document_path,
                ok=True,
                phase=IngestPhase.COMPLETE,
                added=len(inserts),
                updated=len(replacements),
                removed=removed,
                unchanged=unchanged,
                counters=counters,
                diagnostics=diagnostics,
                timings=timings,
            )
        except MemoryStoreError as exc:
            counters.insert_count = len(inserts) or counters.insert_count
            counters.update_count = len(replacements) or counters.update_count
            counters.remove_count = len(removals) or counters.remove_count
            timings.elapsed_ms = _elapsed_ms(started_at)
            self._annotate_ingestion_exception(
                exc,
                path=document_path,
                phase=phase,
                counters=counters,
                diagnostics=diagnostics,
                timings=timings,
            )
            raise
        except Exception as exc:
            counters.insert_count = len(inserts) or counters.insert_count
            counters.update_count = len(replacements) or counters.update_count
            counters.remove_count = len(removals) or counters.remove_count
            timings.elapsed_ms = _elapsed_ms(started_at)
            wrapped: MemoryStoreError
            if phase in {IngestPhase.PERSIST, IngestPhase.CLOSE}:
                wrapped = PersistenceError(f"Document ingest failed during {phase.value}: {exc}")
            else:
                wrapped = IngestionError(f"Document ingest failed during {phase.value}: {exc}")
            raise self._annotate_ingestion_exception(
                wrapped,
                path=document_path,
                phase=phase,
                counters=counters,
                diagnostics=diagnostics,
                timings=timings,
                root_exc=exc,
            ) from exc

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
        root = Path(path)
        if not root.exists() or not root.is_dir():
            raise IngestionError(f"Markdown folder was not found: {root}")

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
                ingest_kwargs: dict[str, Any] = {"dry_run": dry_run}
                if progress_callback is not None and progress_every_chunks > 0:
                    ingest_kwargs["progress_every_chunks"] = progress_every_chunks
                    ingest_kwargs["progress_callback"] = self._wrap_document_progress_callback(
                        progress_callback,
                        relative_path,
                    )

                file_result = self.ingest_document(markdown_file, scope, **ingest_kwargs).model_copy(
                    update={"path": relative_path}
                )

                if not dry_run and strategy is FolderIngestConnectionStrategy.REOPEN_PER_FILE:
                    close_started_at = perf_counter()
                    self.close()
                    self._verify_lock_released()
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
                file_result = self._build_failed_ingest_result(relative_path, exc)
                if (
                    not dry_run
                    and self._should_recover_after_failure(strategy, exc)
                    and not resolved_stop_on_error
                ):
                    close_started_at = perf_counter()
                    self.close()
                    self._verify_lock_released()
                    self._ensure_repository()
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

    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
        return self._lifecycle().promote(memory_id, reason=reason)

    def supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
        reason: str | None = None,
    ) -> None:
        self._lifecycle().supersede(old_memory_id, new_memory_id, reason=reason)

    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None:
        self._lifecycle().contradict(memory_id_a, memory_id_b, reason=reason)

    def expire(self, memory_id: str, reason: str | None = None) -> None:
        self._lifecycle().expire(memory_id, reason=reason)

    def forget(self, memory_id: str) -> None:
        self._privacy().forget(memory_id)

    def forget_by_user(self, user_id: str) -> int:
        return self._privacy().forget_by_user(user_id)

    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
        return self._privacy().delete_by_scope(scope, hard_delete=hard_delete)

    def disable_memory(self, scope: Scope) -> int:
        return self._privacy().disable_memory(scope)

    def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
        return self._privacy().export_user_memories(user_id)

    def export_scope(self, scope: Scope) -> MemoryExport:
        return self._privacy().export_scope(scope)

    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult:
        return self._privacy().import_memories(payload, mode=mode)

    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult:
        return self._privacy().redact(patterns, scope=scope)

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return self._lifecycle().add_feedback(memory_id, feedback)

    def stats(self) -> MemoryStats:
        repository = self._repository()
        total_records = repository.count_memories()
        global_records = repository.count_memories(scope=Scope())

        status_counts = {
            status.value: count
            for status in MemoryStatus
            if (count := repository.count_memories(status=status)) > 0
        }
        type_counts = {
            memory_type.value: count
            for memory_type in MemoryType
            if (count := repository.count_memories(memory_type=memory_type)) > 0
        }

        return MemoryStats(
            total_records=total_records,
            scope_counts={
                "global": global_records,
                "scoped": total_records - global_records,
            },
            status_counts=status_counts,
            type_counts=type_counts,
        )

    def health(self) -> HealthStatus:
        repository = self._repository()
        handle = self._database_handle
        if handle is None:
            raise PersistenceError("Database handle is not available.")

        return HealthStatus(
            status="ok" if handle.database.is_open() else "degraded",
            database_path=handle.database_path,
            schema_version=read_schema_version(repository.database) or self._schema_version,
            dependencies={
                "arcadedb_embedded": arcade_runtime_available(),
                "fastembed": fastembed_runtime_available(),
            },
            message="Core CRUD, health, and stats APIs are ready.",
        )

    def _collect_markdown_files(
        self,
        root: Path,
        *,
        resume_from: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[Path]:
        markdown_files = sorted(
            child
            for child in root.rglob("*")
            if child.is_file() and child.suffix.lower() in {".md", ".markdown"}
        )
        if since is not None:
            markdown_files = [
                file_path
                for file_path in markdown_files
                if (modified_at := self._file_modified_at(file_path)) is not None
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
            return root / ".memory_store_ingest_manifest.json"
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
        manifest.updated_at = _utcnow()
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
            modified_at = self._file_modified_at(file_path)
            entry = entries.get(label)
            if entry is None:
                entries[label] = FolderIngestManifestEntry(
                    path=relative_path,
                    file_size_bytes=self._measure_file_size_bytes(file_path),
                    modified_at=modified_at,
                )
                continue

            entry.path = relative_path
            entry.file_size_bytes = self._measure_file_size_bytes(file_path)
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
        statuses = {
            entry.path.as_posix(): entry.status
            for entry in manifest.files
        }
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
        entry.updated_at = _utcnow()
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

    def _annotate_ingestion_exception(
        self,
        exc: MemoryStoreError,
        *,
        path: Path,
        phase: IngestPhase,
        counters: IngestCounters,
        diagnostics: IngestDiagnostics,
        timings: IngestTimings,
        root_exc: Exception | None = None,
    ) -> MemoryStoreError:
        chain = self._format_exception_chain(root_exc or exc)
        if root_exc is not None:
            chain.insert(0, self._format_exception_summary(exc))

        setattr(exc, "document_path", Path(path))
        setattr(exc, "ingest_phase", phase.value)
        setattr(exc, "ingest_counters", counters.model_dump(mode="python"))
        setattr(exc, "ingest_diagnostics", diagnostics.model_dump(mode="python"))
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

    def _build_document_chunk_memory(
        self,
        *,
        scope: Scope,
        source_path: str,
        parsed_document: Any,
        frontmatter: dict[str, Any],
        heading_path: tuple[str, ...],
        section_index: int,
        chunk_index: int,
        document_chunk_index: int,
        text: str,
        approximate_tokens: int,
        source_hash: str,
        dropped_frontmatter_fields: list[str] | None = None,
    ) -> MemoryCreate:
        metadata = {
            "frontmatter": dict(frontmatter),
            "document_lifecycle": "source_controlled",
            "section_index": section_index,
            "section_chunk_index": chunk_index,
            "document_chunk_index": document_chunk_index,
            "approximate_token_count": approximate_tokens,
            "chunking_max_tokens": self.settings.chunking.max_tokens,
            "chunking_overlap_tokens": self.settings.chunking.overlap_tokens,
            "chunking_max_tokens_is_approximate": True,
            "chunking_preserve_code_blocks": self.settings.chunking.preserve_code_blocks,
        }
        if dropped_frontmatter_fields:
            metadata["frontmatter_dropped_fields"] = list(dropped_frontmatter_fields)
        if heading_path:
            metadata["heading_path_text"] = " > ".join(heading_path)
        if frontmatter.get("owner") is not None:
            metadata["owner"] = frontmatter["owner"]

        base_memory = MemoryCreate(
            scope=scope,
            stable_key=source_hash,
            memory_type=MemoryType.DOCUMENT_CHUNK,
            status=MemoryStatus.ACTIVE,
            title=parsed_document.title,
            summary=parsed_document.description,
            text=text,
            tags=list(parsed_document.tags),
            source_type=SourceType.DOCUMENT,
            source_path=source_path,
            source_hash=source_hash,
            heading_path=list(heading_path) or None,
            section_index=section_index,
            section_chunk_index=chunk_index,
            document_chunk_index=document_chunk_index,
            chunk_index=chunk_index,
            allow_retrieval=True,
            allow_llm_context=True,
            metadata=metadata,
        )

        content_hash = compute_content_hash(
            build_embedding_text(
                base_memory,
                include_heading_path=self.settings.chunking.include_heading_path,
                include_frontmatter=self.settings.chunking.include_frontmatter_in_embedding,
            )
        )
        chunk_id = compute_chunk_id(source_path, heading_path, chunk_index, content_hash)

        return base_memory.model_copy(
            update={
                "chunk_id": chunk_id,
                "metadata": {
                    **metadata,
                    "content_hash": content_hash,
                },
            }
        )

    def _persist_document_ingestion(
        self,
        *,
        inserts: list[MemoryCreate],
        replacements: list[tuple[MemoryRecord, MemoryCreate]],
        removals: list[MemoryRecord],
    ) -> int:
        repository = self._repository()
        removed_count = len(removals)

        operations: list[tuple[str, Any]] = []
        operations.extend(("insert", memory) for memory in inserts)
        operations.extend(("replace", (existing, replacement)) for existing, replacement in replacements)
        operations.extend(("remove", record) for record in removals)

        for batch in self._iter_sized_batches(
            operations,
            self.settings.ingestion.max_chunks_per_transaction,
        ):
            def _operation(batch_operations: list[tuple[str, Any]] = batch) -> None:
                for operation_name, payload in batch_operations:
                    if operation_name == "insert":
                        repository._insert_memory(payload, use_transaction=False)
                        continue

                    if operation_name == "replace":
                        existing, replacement = payload
                        stable_key = (
                            replacement.stable_key or existing.stable_key or existing.source_hash
                        )
                        if stable_key is None:
                            raise PersistenceError(
                                "Document chunk replacement requires a stable logical key."
                            )
                        merged = repository._replace_record(
                            existing,
                            replacement,
                            stable_key=stable_key,
                        )
                        repository._persist_existing(merged, use_transaction=False)
                        continue

                    record = payload
                    if self.settings.chunking.removed_chunk_policy == "hard_delete":
                        repository._delete_memory(record.memory_id, use_transaction=False)
                        continue

                    metadata = dict(record.metadata)
                    metadata["document_removed"] = True
                    repository._update_memory(
                        record.memory_id,
                        MemoryUpdate(
                            status=MemoryStatus.REMOVED,
                            allow_retrieval=False,
                            allow_llm_context=False,
                            metadata=metadata,
                        ),
                        use_transaction=False,
                    )

            run_in_transaction(repository.database, _operation)
        return removed_count

    def _embed_document_candidates(
        self,
        pending_writes: list[tuple[MemoryRecord | None, MemoryCreate]],
        *,
        path: Path,
        total_document_chunks: int,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_every_chunks: int = 0,
    ) -> list[tuple[MemoryRecord | None, MemoryCreate]]:
        if not pending_writes:
            return []

        provider = self._embedding_provider()
        prepared_writes: list[tuple[MemoryRecord | None, MemoryCreate]] = []
        completed_chunks = 0

        for batch in self._iter_sized_batches(
            pending_writes,
            self._document_embedding_batch_size(),
        ):
            texts = [
                build_embedding_text(
                    memory,
                    include_heading_path=self.settings.chunking.include_heading_path,
                    include_frontmatter=self.settings.chunking.include_frontmatter_in_embedding,
                )
                for _existing, memory in batch
            ]
            embedded_batch = provider.embed_batch(texts)

            for (existing, memory), embedded in zip(batch, embedded_batch):
                prepared_writes.append((existing, self._apply_embedded_text(memory, embedded)))

            completed_chunks += len(batch)
            if progress_callback is not None and progress_every_chunks > 0:
                if (
                    completed_chunks % progress_every_chunks == 0
                    or completed_chunks == len(pending_writes)
                ):
                    progress_callback(
                        {
                            "kind": "chunk",
                            "phase": IngestPhase.EMBED.value,
                            "path": path.as_posix(),
                            "completed_chunks": completed_chunks,
                            "total_chunks": len(pending_writes),
                            "document_chunks": total_document_chunks,
                        }
                    )

        return prepared_writes

    def _ensure_repository(self) -> None:
        if self._database_handle is not None and self._repository_instance is not None:
            return

        embedding_dimensions = self.settings.embeddings.dim
        if embedding_dimensions is None:
            raise PersistenceError(
                "MemoryStoreSettings.embeddings.dim must be set to initialize the database schema."
            )

        handle = open_arcade_database(
            ArcadeConnectionSettings.from_database_settings(self.settings.database)
        )

        try:
            self._schema_version = ensure_database_schema(
                handle.database,
                expected_version=self.settings.database.schema_version,
                embedding_dimensions=embedding_dimensions,
            )
            self._active_embedding_dimension = validate_active_index_dimension(
                handle.database,
                embedding_dimensions,
            )
        except Exception:
            with suppress(PersistenceError):
                handle.close()
            raise

        self._database_handle = handle
        self._repository_instance = ArcadeMemoryRepository(
            handle.database,
            schema_version=self._schema_version,
        )
        self._lifecycle_manager_instance = None
        self._privacy_controller_instance = None

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

    def _document_embedding_batch_size(self) -> int:
        configured = self.settings.ingestion.max_chunks_per_document_batch
        if configured is not None:
            return configured
        return self.settings.embeddings.batch_size

    def _iter_sized_batches(self, values: list[Any], batch_size: int) -> list[list[Any]]:
        if batch_size <= 0:
            raise PersistenceError("Batch size must be greater than zero.")
        return [values[index : index + batch_size] for index in range(0, len(values), batch_size)]

    def _measure_file_size_bytes(self, path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _file_modified_at(self, path: Path) -> datetime | None:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None

    def _measure_frontmatter_bytes(self, source_text: str) -> int:
        if not source_text.startswith("---"):
            return 0

        lines = source_text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            return 0

        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return len("".join(lines[1:index]).encode("utf-8"))
        return 0

    def _sanitize_frontmatter_metadata(
        self,
        frontmatter: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        sanitized: dict[str, Any] = {}
        dropped: list[str] = []

        for key, value in frontmatter.items():
            self._flatten_metadata_value(str(key), value, sanitized, dropped)

        return sanitized, self._deduplicate_strings(dropped)

    def _flatten_metadata_value(
        self,
        key: str,
        value: Any,
        target: dict[str, Any],
        dropped: list[str],
    ) -> None:
        if self._is_supported_metadata_scalar(value):
            target[key] = value
            return

        if isinstance(value, list):
            if all(self._is_supported_metadata_scalar(item) for item in value):
                target[key] = list(value)
            else:
                dropped.append(key)
            return

        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                self._flatten_metadata_value(f"{key}.{nested_key}", nested_value, target, dropped)
            return

        dropped.append(key)

    def _is_supported_metadata_scalar(self, value: Any) -> bool:
        return isinstance(value, (str, int, float, bool))

    def _deduplicate_strings(self, values: list[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            unique_values.append(value)
        return unique_values

    def _collect_ingestion_limit_violations(
        self,
        *,
        file_size_bytes: int,
        frontmatter_bytes: int,
        section_count: int,
        chunk_count: int,
    ) -> list[str]:
        violations: list[str] = []
        limit_checks = [
            (
                "file_size_bytes",
                file_size_bytes,
                self.settings.ingestion.max_file_size_bytes,
                "max_file_size_bytes",
            ),
            (
                "frontmatter_bytes",
                frontmatter_bytes,
                self.settings.ingestion.max_frontmatter_bytes,
                "max_frontmatter_bytes",
            ),
            (
                "section_count",
                section_count,
                self.settings.ingestion.max_sections,
                "max_sections",
            ),
            (
                "chunk_count",
                chunk_count,
                self.settings.ingestion.max_chunks,
                "max_chunks",
            ),
        ]

        for label, value, limit, limit_label in limit_checks:
            if limit is not None and value > limit:
                violations.append(f"{label}={value} exceeds {limit_label}={limit}")

        return violations

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

    def _embedding_provider(self) -> FastEmbedProvider:
        if self._embedding_provider_instance is not None:
            return self._embedding_provider_instance

        provider_name = self.settings.embeddings.provider.lower()
        if provider_name != "fastembed":
            raise PersistenceError(
                f"Unsupported embedding provider: {self.settings.embeddings.provider}"
            )

        self._embedding_provider_instance = FastEmbedProvider(
            model=self.settings.embeddings.model,
            model_version=self.settings.embeddings.model_version,
            batch_size=self.settings.embeddings.batch_size,
            normalize=self.settings.embeddings.normalize,
        )
        return self._embedding_provider_instance

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
