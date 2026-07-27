"""Focused read-side application service for direct memory queries."""

from __future__ import annotations

from typing import Any

from ..models import MemoryRecord


class MemoryQueryService:
    """Owns point lookups while reusing the shared runtime helpers."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return self._owner._repository().get_memory(memory_id)