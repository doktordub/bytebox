"""Provider-independent application ports for the ByteBox core."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol, TypeVar

from ..domain import (
    ChunkSearchResult,
    HealthStatus,
    ImportMode,
    ImportResult,
    MemoryCreate,
    MemoryExport,
    MemoryFeedback,
    MemoryImport,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStats,
    MemoryUpdate,
    RedactionResult,
    Scope,
)
from ..domain.value_objects import EmbeddedVector

_T = TypeVar("_T")


class MemoryRepositoryPort(Protocol):
    def insert_memory(self, memory: MemoryCreate) -> MemoryRecord: ...
    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord: ...
    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
    ) -> MemoryRecord: ...
    def get_memory(self, memory_id: str) -> MemoryRecord | None: ...
    def list_by_scope(self, scope: Scope) -> list[MemoryRecord]: ...
    def list_by_source_path(
        self,
        source_path: str,
        *,
        scope: Scope,
        memory_type: object | None = None,
    ) -> list[MemoryRecord]: ...
    def get_chunk_by_id(self, chunk_id: str, *, scope: Scope | None = None) -> MemoryRecord | None: ...
    def list_chunk_window(
        self,
        source_path: str,
        *,
        scope: Scope,
        document_chunk_index: int,
        before: int,
        after: int,
    ) -> list[MemoryRecord]: ...
    def read_one_hop_links(self, memory_id: str) -> list[MemoryRecord]: ...
    def count_memories(self, scope: Scope | None = None, **filters: Any) -> int: ...


class UnitOfWorkPort(Protocol):
    def run(self, operation: Callable[[], _T]) -> _T: ...


class EmbeddingProviderPort(Protocol):
    def embed_text(self, text: str) -> EmbeddedVector: ...
    def embed_batch(self, texts: list[str]) -> list[EmbeddedVector]: ...


class RerankerPort(Protocol):
    def rerank(self, query: str, documents: list[str], batch_size: int = 32, **kwargs: Any) -> list[float]: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class IdGeneratorPort(Protocol):
    def new_id(self, prefix: str | None = None) -> str: ...


class TelemetryPort(Protocol):
    def emit(self, event: str, **attributes: Any) -> None: ...


class MemoryCommandPort(Protocol):
    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord: ...
    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord: ...
    def upsert_memory(
        self,
        memory: MemoryCreate,
        stable_key: str | None = None,
        *,
        embed: bool = False,
    ) -> MemoryRecord: ...


class MemoryQueryPort(Protocol):
    def get_memory(self, memory_id: str) -> MemoryRecord | None: ...
    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]: ...
    def search_document_chunks(
        self,
        *,
        text: str,
        scope: Scope,
        limit: int = 10,
        before: int = 0,
        after: int = 0,
        include_removed: bool = False,
        allow_retrieval_only: bool = True,
    ) -> list[ChunkSearchResult]: ...


class LifecyclePort(Protocol):
    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord: ...
    def supersede(self, old_memory_id: str, new_memory_id: str, reason: str | None = None) -> None: ...
    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None: ...
    def expire(self, memory_id: str, reason: str | None = None) -> None: ...
    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord: ...


class PrivacyPort(Protocol):
    def forget(self, memory_id: str) -> None: ...
    def forget_by_user(self, user_id: str) -> int: ...
    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int: ...
    def disable_memory(self, scope: Scope) -> int: ...
    def export_user_memories(self, user_id: str) -> list[MemoryRecord]: ...
    def export_scope(self, scope: Scope) -> MemoryExport: ...
    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult: ...
    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult: ...


class AdministrationPort(Protocol):
    def stats(self) -> MemoryStats: ...
    def health(self) -> HealthStatus: ...