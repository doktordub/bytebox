"""Focused application service for lifecycle operations."""

from __future__ import annotations

from typing import Any

from ..models import MemoryFeedback, MemoryRecord


class LifecycleService:
    """Owns promotion, contradiction, expiration, and feedback flows."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
        return self._owner._lifecycle().promote(memory_id, reason=reason)

    def supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
        reason: str | None = None,
    ) -> None:
        self._owner._lifecycle().supersede(old_memory_id, new_memory_id, reason=reason)

    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None:
        self._owner._lifecycle().contradict(memory_id_a, memory_id_b, reason=reason)

    def expire(self, memory_id: str, reason: str | None = None) -> None:
        self._owner._lifecycle().expire(memory_id, reason=reason)

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        return self._owner._lifecycle().add_feedback(memory_id, feedback)