"""Focused application service for privacy and data-control operations."""

from __future__ import annotations

from typing import Any

from ..models import (
    ImportMode,
    ImportResult,
    MemoryExport,
    MemoryImport,
    MemoryRecord,
    RedactionResult,
    Scope,
)


class PrivacyService:
    """Owns forget, delete, export, import, and redaction flows."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def forget(self, memory_id: str) -> None:
        self._owner._privacy().forget(memory_id)

    def forget_by_user(self, user_id: str) -> int:
        return self._owner._privacy().forget_by_user(user_id)

    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
        return self._owner._privacy().delete_by_scope(scope, hard_delete=hard_delete)

    def disable_memory(self, scope: Scope) -> int:
        return self._owner._privacy().disable_memory(scope)

    def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
        return self._owner._privacy().export_user_memories(user_id)

    def export_scope(self, scope: Scope) -> MemoryExport:
        return self._owner._privacy().export_scope(scope)

    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult:
        return self._owner._privacy().import_memories(payload, mode=mode)

    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult:
        return self._owner._privacy().redact(patterns, scope=scope)