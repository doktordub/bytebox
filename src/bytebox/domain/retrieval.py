"""Search and chunk retrieval models."""

from __future__ import annotations

from typing import Any, Self

from pydantic import AliasChoices, Field, field_validator

from .base import (
    MemoryModel,
    MemoryStatus,
    MemoryType,
    ScopedMemoryModel,
    SensitivityLevel,
    SourceType,
    _validate_finite_score_map,
    _validate_normalized_score_map,
)
from .memory import MemoryRecord


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