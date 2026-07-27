"""Document-scoped ingestion workflows and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from ..embeddings import build_embedding_text
from ..errors import IngestionError, MemoryStoreError, PersistenceError
from ..ingest_security import resolve_ingest_path
from ..ingestion import (
    chunk_markdown_sections,
    compute_chunk_id,
    compute_content_hash,
    compute_source_hash,
    normalize_source_path,
    read_markdown_file,
)
from ..domain.value_objects import PendingDocumentWrite
from ..models import (
    IngestCounters,
    IngestDiagnostics,
    IngestPhase,
    IngestResult,
    IngestTimings,
    MemoryCreate,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    MemoryUpdate,
    Scope,
    SourceType,
)


def _elapsed_ms(started_at: float) -> int:
    return int(round((perf_counter() - started_at) * 1000))


class DocumentIngestWorker:
    """Owns single-document chunking, embedding, and persistence flows."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def ingest_document(
        self,
        path: str | Path,
        scope: Scope,
        *,
        dry_run: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_every_chunks: int = 0,
    ) -> IngestResult:
        document_path = resolve_ingest_path(
            path,
            ingest_roots=self._owner.settings.security.ingest_roots,
            allow_symlinks=self._owner.settings.security.allow_symlinks,
            expect_directory=False,
        )
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
                strategy=self._owner.settings.chunking.strategy,
                max_tokens=self._owner.settings.chunking.max_tokens,
                overlap_tokens=self._owner.settings.chunking.overlap_tokens,
                include_heading_path=self._owner.settings.chunking.include_heading_path,
                preserve_code_blocks=self._owner.settings.chunking.preserve_code_blocks,
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

            repository = self._owner._repository()
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

            pending_writes: list[PendingDocumentWrite] = []
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

                pending_writes.append(
                    PendingDocumentWrite(
                        existing=existing,
                        candidate=candidate,
                    )
                )

            removals = [
                record
                for source_hash, record in existing_by_source_hash.items()
                if source_hash not in planned_source_hashes
                and record.status not in {MemoryStatus.REMOVED, MemoryStatus.DELETED}
            ]
            counters.insert_count = sum(1 for pending_write in pending_writes if pending_write.is_insert)
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

            for pending_write in prepared_writes:
                if pending_write.is_insert:
                    inserts.append(pending_write.candidate)
                    continue
                if pending_write.existing is None:
                    raise PersistenceError("Document replacement requires an existing memory record.")
                replacements.append((pending_write.existing, pending_write.candidate))

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
            self._owner._annotate_ingestion_exception(
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
            raise self._owner._annotate_ingestion_exception(
                wrapped,
                path=document_path,
                phase=phase,
                counters=counters,
                diagnostics=diagnostics,
                timings=timings,
                root_exc=exc,
            ) from exc

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
            "chunking_max_tokens": self._owner.settings.chunking.max_tokens,
            "chunking_overlap_tokens": self._owner.settings.chunking.overlap_tokens,
            "chunking_max_tokens_is_approximate": True,
            "chunking_preserve_code_blocks": self._owner.settings.chunking.preserve_code_blocks,
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
                include_heading_path=self._owner.settings.chunking.include_heading_path,
                include_frontmatter=self._owner.settings.chunking.include_frontmatter_in_embedding,
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
        repository = self._owner._repository()
        removed_count = len(removals)

        operations: list[tuple[str, Any]] = []
        operations.extend(("insert", memory) for memory in inserts)
        operations.extend(("replace", (existing, replacement)) for existing, replacement in replacements)
        operations.extend(("remove", record) for record in removals)

        for batch in self._iter_sized_batches(
            operations,
            self._owner.settings.ingestion.max_chunks_per_transaction,
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
                    if self._owner.settings.chunking.removed_chunk_policy == "hard_delete":
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

            self._owner._run_in_transaction(repository.database, _operation)
        return removed_count

    def _embed_document_candidates(
        self,
        pending_writes: list[PendingDocumentWrite],
        *,
        path: Path,
        total_document_chunks: int,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_every_chunks: int = 0,
    ) -> list[PendingDocumentWrite]:
        if not pending_writes:
            return []

        provider = self._owner._embedding_provider()
        prepared_writes: list[PendingDocumentWrite] = []
        completed_chunks = 0

        for batch in self._iter_sized_batches(
            pending_writes,
            self._document_embedding_batch_size(),
        ):
            texts = [
                build_embedding_text(
                    pending_write.candidate,
                    include_heading_path=self._owner.settings.chunking.include_heading_path,
                    include_frontmatter=self._owner.settings.chunking.include_frontmatter_in_embedding,
                )
                for pending_write in batch
            ]
            embedded_batch = provider.embed_batch(texts)

            for pending_write, embedded in zip(batch, embedded_batch):
                prepared_writes.append(
                    PendingDocumentWrite(
                        existing=pending_write.existing,
                        candidate=self._owner._apply_embedded_text(
                            pending_write.candidate,
                            embedded,
                        ),
                    )
                )

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

    def _document_embedding_batch_size(self) -> int:
        configured = self._owner.settings.ingestion.max_chunks_per_document_batch
        if configured is not None:
            return configured
        return self._owner.settings.embeddings.batch_size

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
                self._owner.settings.ingestion.max_file_size_bytes,
                "max_file_size_bytes",
            ),
            (
                "frontmatter_bytes",
                frontmatter_bytes,
                self._owner.settings.ingestion.max_frontmatter_bytes,
                "max_frontmatter_bytes",
            ),
            (
                "section_count",
                section_count,
                self._owner.settings.ingestion.max_sections,
                "max_sections",
            ),
            (
                "chunk_count",
                chunk_count,
                self._owner.settings.ingestion.max_chunks,
                "max_chunks",
            ),
        ]

        for label, value, limit, limit_label in limit_checks:
            if limit is not None and value > limit:
                violations.append(f"{label}={value} exceeds {limit_label}={limit}")

        return violations