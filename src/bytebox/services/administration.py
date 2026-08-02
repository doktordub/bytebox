"""Focused application service for health and statistics surfaces."""

from __future__ import annotations

from typing import Any

from ..arcade import arcade_runtime_available, read_schema_version
from ..embeddings import fastembed_runtime_available
from ..errors import PersistenceError
from ..models import (
    HealthStatus,
    InventoryDetailLevel,
    MemoryInventoryQuery,
    MemoryInventoryReport,
    MemoryInventorySummary,
    MemoryStats,
    MemoryType,
    MemoryTypeInventory,
    ScopeInventory,
)


def _display_name(memory_type: MemoryType) -> str:
    return memory_type.value.replace("_", " ").title()


def _inventory_memory_types(*, include_document_chunks: bool) -> tuple[MemoryType, ...]:
    return tuple(
        memory_type
        for memory_type in MemoryType
        if include_document_chunks or memory_type != MemoryType.DOCUMENT_CHUNK
    )


class AdministrationService:
    """Owns operator-friendly status and aggregate counters."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def stats(self) -> MemoryStats:
        report = self.inventory(detail=InventoryDetailLevel.SUMMARY)
        return MemoryStats.model_validate(report.summary.model_dump(mode="python"))

    def inventory(
        self,
        *,
        detail: InventoryDetailLevel | str = InventoryDetailLevel.SUMMARY,
        include_names: bool = False,
        names_limit: int = 100,
        include_document_chunks: bool = True,
    ) -> MemoryInventoryReport:
        repository = self._owner._repository()
        query = MemoryInventoryQuery(
            detail=detail,
            include_names=include_names,
            names_limit=names_limit,
            include_document_chunks=include_document_chunks,
        )
        summary = MemoryInventorySummary.model_validate(
            repository.aggregate_inventory_summary(
                include_document_chunks=query.include_document_chunks,
            )
        )
        if query.detail == InventoryDetailLevel.SUMMARY:
            return MemoryInventoryReport(detail=query.detail, summary=summary)

        scopes = ScopeInventory.model_validate(
            repository.aggregate_inventory_scopes(
                include_names=query.include_names,
                names_limit=query.names_limit,
                include_document_chunks=query.include_document_chunks,
            )
        )
        raw_types = {
            item["memory_type"]: item
            for item in repository.aggregate_inventory_memory_types(
                include_document_chunks=query.include_document_chunks,
            )
        }
        memory_types = [
            MemoryTypeInventory(
                memory_type=memory_type,
                display_name=_display_name(memory_type),
                count=int(raw_types.get(memory_type.value, {}).get("count", 0)),
                status_counts=dict(raw_types.get(memory_type.value, {}).get("status_counts", {})),
                scope_counts=dict(
                    raw_types.get(memory_type.value, {}).get(
                        "scope_counts",
                        {"global": 0, "scoped": 0},
                    )
                ),
                oldest_created_at=raw_types.get(memory_type.value, {}).get("oldest_created_at"),
                newest_updated_at=raw_types.get(memory_type.value, {}).get("newest_updated_at"),
            )
            for memory_type in _inventory_memory_types(
                include_document_chunks=query.include_document_chunks,
            )
        ]
        return MemoryInventoryReport(
            detail=query.detail,
            summary=summary,
            scopes=scopes,
            memory_types=memory_types,
        )

    def health(self) -> HealthStatus:
        repository = self._owner._repository()
        handle = self._owner._database_handle
        if handle is None:
            raise PersistenceError("Database handle is not available.")

        return HealthStatus(
            status="ok" if handle.database.is_open() else "degraded",
            database_path=handle.database_path,
            schema_version=read_schema_version(repository.database) or self._owner._schema_version,
            dependencies={
                "arcadedb_embedded": arcade_runtime_available(),
                "fastembed": fastembed_runtime_available(),
            },
            message="Core CRUD, health, and stats APIs are ready.",
        )