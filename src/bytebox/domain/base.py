"""Shared Pydantic base models, enums, and validation helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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