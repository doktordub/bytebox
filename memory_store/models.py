"""Typed domain models for configuration, persistence, and retrieval flows."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _synchronize_section_chunk_index(model: Self) -> Self:
    chunk_index = getattr(model, "chunk_index", None)
    section_chunk_index = getattr(model, "section_chunk_index", None)

    if (
        chunk_index is not None
        and section_chunk_index is not None
        and chunk_index != section_chunk_index
    ):
        raise ValueError("chunk_index must match section_chunk_index when both are provided.")

    resolved_chunk_index = chunk_index if chunk_index is not None else section_chunk_index
    if resolved_chunk_index is not None:
        model.chunk_index = resolved_chunk_index
        model.section_chunk_index = resolved_chunk_index
    return model


def _validate_finite_score_map(values: dict[str, float]) -> dict[str, float]:
    for name, score in values.items():
        if not math.isfinite(score):
            raise ValueError(f"Score '{name}' must be finite.")
    return values


def _validate_normalized_score_map(values: dict[str, float]) -> dict[str, float]:
    for name, score in values.items():
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Normalized score '{name}' must be between 0 and 1.")
    return values


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class Scope(MemoryModel):
    user_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None

    @field_validator("user_id", "project_id", "agent_id", mode="before")
    @classmethod
    def normalize_empty_strings(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_global(self) -> bool:
        return not any((self.user_id, self.project_id, self.agent_id))


class MemoryType(StrEnum):
    USER_PREFERENCE = "user_preference"
    PROJECT_FACT = "project_fact"
    TASK_STATE = "task_state"
    CONVERSATION_SUMMARY = "conversation_summary"
    DECISION = "decision"
    OBSERVATION = "observation"
    ERROR_DEBUG_NOTE = "error_debug_note"
    DOCUMENT_CHUNK = "document_chunk"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    EXPIRED = "expired"
    DELETED = "deleted"
    REMOVED = "removed"
    FORGOTTEN = "forgotten"


class SensitivityLevel(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class SourceType(StrEnum):
    MANUAL = "manual"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    IMPORT = "import"


class IngestPhase(StrEnum):
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    PERSIST = "persist"
    CLOSE = "close"
    COMPLETE = "complete"


class FolderIngestConnectionStrategy(StrEnum):
    SHARED_STORE = "shared_store"
    REOPEN_PER_FILE = "reopen_per_file"
    REOPEN_ON_FAILURE = "reopen_on_failure"


class FolderIngestManifestStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ImportMode = Literal["insert", "replace", "upsert"]

_SCOPE_FIELDS = ("user_id", "project_id", "agent_id")


class ScopedMemoryModel(MemoryModel):
    scope: Scope = Field(default_factory=Scope)
    user_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None

    @model_validator(mode="after")
    def synchronize_scope(self) -> Self:
        resolved: dict[str, str | None] = {}

        for field_name in _SCOPE_FIELDS:
            scoped_value = getattr(self.scope, field_name)
            direct_value = getattr(self, field_name)
            if direct_value is not None and scoped_value is not None and direct_value != scoped_value:
                raise ValueError(
                    f"{field_name} must match scope.{field_name} when both are provided."
                )
            resolved[field_name] = direct_value if direct_value is not None else scoped_value

        self.scope = Scope(**resolved)
        self.user_id = resolved["user_id"]
        self.project_id = resolved["project_id"]
        self.agent_id = resolved["agent_id"]
        return self


class MemoryCreate(ScopedMemoryModel):
    stable_key: str | None = None
    memory_type: MemoryType = MemoryType.OBSERVATION
    status: MemoryStatus = MemoryStatus.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    title: str | None = None
    summary: str | None = None
    text: str = Field(min_length=1, validation_alias=AliasChoices("text", "content"))
    tags: list[str] = Field(default_factory=list)
    source_type: SourceType | None = SourceType.MANUAL
    source_path: str | None = None
    source_hash: str | None = None
    source_uri: str | None = None
    chunk_id: str | None = None
    heading_path: list[str] | None = None
    section_index: int | None = Field(default=None, ge=0)
    section_chunk_index: int | None = Field(default=None, ge=0)
    document_chunk_index: int | None = Field(default=None, ge=0)
    chunk_index: int | None = Field(default=None, ge=0)
    embedding: list[float] | None = None
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    embedding_dim: int | None = Field(default=None, ge=1)
    embedding_created_at: datetime | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    user_rating: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    expires_at: datetime | None = None
    allow_retrieval: bool = True
    allow_llm_context: bool = True
    retention_policy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def synchronize_chunk_positions(self) -> Self:
        return _synchronize_section_chunk_index(self)

    @model_validator(mode="after")
    def validate_embedding_shape(self) -> Self:
        if self.embedding is None:
            return self
        if self.embedding_dim is None:
            self.embedding_dim = len(self.embedding)
            return self
        if self.embedding_dim != len(self.embedding):
            raise ValueError("embedding_dim must match the number of embedding values.")
        return self

    @property
    def content(self) -> str:
        return self.text


class MemoryUpdate(MemoryModel):
    stable_key: str | None = None
    status: MemoryStatus | None = None
    sensitivity: SensitivityLevel | None = None
    title: str | None = None
    summary: str | None = None
    text: str | None = Field(default=None, validation_alias=AliasChoices("text", "content"))
    tags: list[str] | None = None
    source_type: SourceType | None = None
    source_path: str | None = None
    source_hash: str | None = None
    source_uri: str | None = None
    chunk_id: str | None = None
    heading_path: list[str] | None = None
    section_index: int | None = Field(default=None, ge=0)
    section_chunk_index: int | None = Field(default=None, ge=0)
    document_chunk_index: int | None = Field(default=None, ge=0)
    chunk_index: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    user_rating: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    expires_at: datetime | None = None
    allow_retrieval: bool | None = None
    allow_llm_context: bool | None = None
    retention_policy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def synchronize_chunk_positions(self) -> Self:
        return _synchronize_section_chunk_index(self)

    @property
    def content(self) -> str | None:
        return self.text


class MemoryRecord(MemoryCreate):
    memory_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_accessed_at: datetime | None = None
    version: int = Field(default=1, ge=1)
    schema_version: int = Field(default=1, ge=1)
    superseded_by: str | None = None


class MemorySearchQuery(ScopedMemoryModel):
    text: str = Field(min_length=1, validation_alias=AliasChoices("text", "query"))
    limit: int = Field(default=10, ge=1)
    memory_types: list[MemoryType] | None = None
    statuses: list[MemoryStatus] | None = None
    source_types: list[SourceType] | None = None
    sensitivity: SensitivityLevel | None = None
    include_removed: bool = False
    include_forgotten: bool = False
    allow_retrieval_only: bool = True

    @property
    def query(self) -> str:
        return self.text


class ChunkSearchQuery(ScopedMemoryModel):
    text: str = Field(min_length=1, validation_alias=AliasChoices("text", "query"))
    limit: int = Field(default=10, ge=1)
    before: int = Field(default=0, ge=0)
    after: int = Field(default=0, ge=0)
    include_removed: bool = False
    allow_retrieval_only: bool = True

    @property
    def query(self) -> str:
        return self.text


class MemorySearchResult(MemoryModel):
    memory: MemoryRecord = Field(validation_alias=AliasChoices("memory", "record"))
    final_score: float = Field(ge=0.0, le=1.0)
    component_scores: dict[str, float] = Field(default_factory=dict)
    normalized_scores: dict[str, float] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_scores", "normalized_scores")
    @classmethod
    def validate_score_map(cls, values: dict[str, float]) -> dict[str, float]:
        return _validate_finite_score_map(values)

    @field_validator("normalized_scores")
    @classmethod
    def validate_normalized_scores(cls, values: dict[str, float]) -> dict[str, float]:
        return _validate_normalized_score_map(values)

    @property
    def record(self) -> MemoryRecord:
        return self.memory


class ChunkSearchResult(MemoryModel):
    memory_id: str
    chunk_id: str
    source_path: str | None = None
    source_hash: str | None = None
    title: str | None = None
    summary: str | None = None
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    heading_path: list[str] | None = None
    section_index: int | None = Field(default=None, ge=0)
    section_chunk_index: int | None = Field(default=None, ge=0)
    document_chunk_index: int | None = Field(default=None, ge=0)
    final_score: float = Field(default=1.0, ge=0.0, le=1.0)
    component_scores: dict[str, float] = Field(default_factory=dict)
    normalized_scores: dict[str, float] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_scores", "normalized_scores")
    @classmethod
    def validate_score_map(cls, values: dict[str, float]) -> dict[str, float]:
        return _validate_finite_score_map(values)

    @field_validator("normalized_scores")
    @classmethod
    def validate_normalized_scores(cls, values: dict[str, float]) -> dict[str, float]:
        return _validate_normalized_score_map(values)

    @classmethod
    def from_record(
        cls,
        memory: MemoryRecord,
        *,
        final_score: float = 1.0,
        component_scores: dict[str, float] | None = None,
        normalized_scores: dict[str, float] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> Self:
        return cls(
            memory_id=memory.memory_id,
            chunk_id=memory.chunk_id or memory.memory_id,
            source_path=memory.source_path,
            source_hash=memory.source_hash,
            title=memory.title,
            summary=memory.summary,
            text=memory.text,
            tags=list(memory.tags),
            heading_path=list(memory.heading_path) if memory.heading_path is not None else None,
            section_index=memory.section_index,
            section_chunk_index=(
                memory.section_chunk_index
                if memory.section_chunk_index is not None
                else memory.chunk_index
            ),
            document_chunk_index=memory.document_chunk_index,
            final_score=final_score,
            component_scores=dict(component_scores or {}),
            normalized_scores=dict(normalized_scores or {}),
            debug=dict(debug or {}),
            metadata=dict(memory.metadata),
        )

    @classmethod
    def from_search_result(cls, result: MemorySearchResult) -> Self:
        return cls.from_record(
            result.memory,
            final_score=result.final_score,
            component_scores=result.component_scores,
            normalized_scores=result.normalized_scores,
            debug=result.debug,
        )


class ChunkContextResponse(MemoryModel):
    chunk: ChunkSearchResult
    before: list[ChunkSearchResult] = Field(default_factory=list)
    after: list[ChunkSearchResult] = Field(default_factory=list)


class MemoryFeedback(MemoryModel):
    positive: bool | None = None
    confirmed: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    user_rating: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None


class IngestTimings(MemoryModel):
    parse_ms: int = Field(default=0, ge=0)
    chunk_ms: int = Field(default=0, ge=0)
    embed_ms: int = Field(default=0, ge=0)
    persist_ms: int = Field(default=0, ge=0)
    close_ms: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class IngestCounters(MemoryModel):
    section_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    insert_count: int = Field(default=0, ge=0)
    update_count: int = Field(default=0, ge=0)
    remove_count: int = Field(default=0, ge=0)


class IngestDiagnostics(MemoryModel):
    dry_run: bool = False
    file_size_bytes: int = Field(default=0, ge=0)
    frontmatter_bytes: int = Field(default=0, ge=0)
    frontmatter_keys: list[str] = Field(default_factory=list)
    dropped_metadata_fields: list[str] = Field(default_factory=list)
    limit_violations: list[str] = Field(default_factory=list)


class IngestResult(MemoryModel):
    path: Path
    ok: bool = True
    phase: IngestPhase = IngestPhase.COMPLETE
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    exception_class: str | None = None
    error: str | None = None
    exception_chain: list[str] = Field(default_factory=list)
    counters: IngestCounters = Field(default_factory=IngestCounters)
    diagnostics: IngestDiagnostics = Field(default_factory=IngestDiagnostics)
    timings: IngestTimings = Field(default_factory=IngestTimings)


class FolderIngestManifestEntry(MemoryModel):
    path: Path
    status: FolderIngestManifestStatus = FolderIngestManifestStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=_utcnow)
    file_size_bytes: int = Field(default=0, ge=0)
    modified_at: datetime | None = None
    error: str | None = None
    exception_class: str | None = None
    counters: IngestCounters = Field(default_factory=IngestCounters)
    timings: IngestTimings = Field(default_factory=IngestTimings)


class FolderIngestManifest(MemoryModel):
    root: Path
    updated_at: datetime = Field(default_factory=_utcnow)
    status_counts: dict[str, int] = Field(default_factory=dict)
    files: list[FolderIngestManifestEntry] = Field(default_factory=list)


class FolderIngestResult(MemoryModel):
    root: Path
    ok: bool = True
    connection_strategy: FolderIngestConnectionStrategy = (
        FolderIngestConnectionStrategy.REOPEN_ON_FAILURE
    )
    manifest_path: Path | None = None
    resume_from: str | None = None
    stop_on_error: bool = False
    stopped_on_error: bool = False
    only_failed: bool = False
    limit: int | None = Field(default=None, ge=1)
    since: datetime | None = None
    matched_files: int = Field(default=0, ge=0)
    skipped_files: int = Field(default=0, ge=0)
    files_processed: int = Field(default=0, ge=0)
    failed_files: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    files: list[IngestResult] = Field(default_factory=list)


class MemoryExport(MemoryModel):
    scope: Scope | None = None
    records: list[MemoryRecord] = Field(default_factory=list)
    exported_at: datetime = Field(default_factory=_utcnow)


class MemoryImport(MemoryModel):
    records: list[MemoryRecord] = Field(default_factory=list)
    source: str | None = None


class ImportResult(MemoryModel):
    inserted: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class RedactionResult(MemoryModel):
    redacted: int = Field(default=0, ge=0)
    patterns: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)


class MemoryStats(MemoryModel):
    total_records: int = Field(default=0, ge=0)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    type_counts: dict[str, int] = Field(default_factory=dict)


class HealthStatus(MemoryModel):
    status: str
    database_path: Path
    schema_version: int = Field(ge=1)
    dependencies: dict[str, bool] = Field(default_factory=dict)
    message: str = ""
