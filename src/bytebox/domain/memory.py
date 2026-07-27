"""Memory record and mutation models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import AliasChoices, Field, model_validator

from .base import (
    MemoryModel,
    MemoryStatus,
    MemoryType,
    ScopedMemoryModel,
    SensitivityLevel,
    SourceType,
    _synchronize_section_chunk_index,
    _utcnow,
)


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


class MemoryFeedback(MemoryModel):
    positive: bool | None = None
    confirmed: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    user_rating: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None