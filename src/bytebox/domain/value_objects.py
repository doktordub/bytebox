"""Internal value objects independent of Pydantic and infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .memory import MemoryCreate, MemoryRecord


@dataclass(frozen=True)
class EmbeddedVector:
    vector: list[float]
    model: str
    model_version: str | None
    dim: int
    created_at: datetime


@dataclass(frozen=True)
class PendingDocumentWrite:
    existing: MemoryRecord | None
    candidate: MemoryCreate

    @property
    def is_insert(self) -> bool:
        return self.existing is None