"""Privacy, import/export, and redaction models."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import MemoryModel, Scope, _utcnow
from .memory import MemoryRecord


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