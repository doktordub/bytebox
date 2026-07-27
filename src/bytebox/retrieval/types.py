"""Shared retrieval pipeline data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import MemoryRecord, MemorySearchResult


@dataclass(slots=True)
class RetrievalMatch:
    memory: MemoryRecord
    score: float
    source: str
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalCandidate:
    memory: MemoryRecord
    component_scores: dict[str, float] = field(default_factory=dict)
    normalized_scores: dict[str, float] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    final_score: float = 0.0

    def to_result(
        self,
        *,
        include_component_scores: bool = True,
        include_debug: bool = True,
    ) -> MemorySearchResult:
        return MemorySearchResult(
            memory=self.memory,
            final_score=self.final_score,
            component_scores=self.component_scores if include_component_scores else {},
            normalized_scores=self.normalized_scores if include_component_scores else {},
            debug=self.debug if include_debug else {},
        )