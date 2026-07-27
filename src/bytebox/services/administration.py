"""Focused application service for health and statistics surfaces."""

from __future__ import annotations

from typing import Any

from ..arcade import arcade_runtime_available, read_schema_version
from ..embeddings import fastembed_runtime_available
from ..errors import PersistenceError
from ..models import HealthStatus, MemoryStats


class AdministrationService:
    """Owns operator-friendly status and aggregate counters."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def stats(self) -> MemoryStats:
        repository = self._owner._repository()
        return MemoryStats.model_validate(repository.aggregate_stats())

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