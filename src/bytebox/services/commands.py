"""Focused write-side application service for memory commands."""

from __future__ import annotations

from typing import Any

from ..models import MemoryCreate, MemoryRecord, MemoryUpdate


class MemoryCommandService:
    """Owns CRUD write flows while reusing the shared runtime helpers."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord:
        prepared = self._owner._prepare_memory_for_write(
            self._owner._apply_service_defaults(memory),
            embed=embed,
        )
        return self._owner._repository().insert_memory(prepared)

    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        return self._owner._repository().update_memory(memory_id, patch)

    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
        *,
        embed: bool = False,
    ) -> MemoryRecord:
        return self._owner._repository().upsert_memory(
            self._owner._prepare_memory_for_write(
                self._owner._apply_service_defaults(memory),
                embed=embed,
            ),
            stable_key=stable_key,
        )