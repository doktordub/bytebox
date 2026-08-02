"""Administrative health and statistics models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from .base import MemoryModel, MemoryType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStats(MemoryModel):
    total_records: int = Field(default=0, ge=0)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    type_counts: dict[str, int] = Field(default_factory=dict)


class InventoryDetailLevel(StrEnum):
    SUMMARY = "summary"
    FULL = "full"


class MemoryInventoryQuery(MemoryModel):
    detail: InventoryDetailLevel = InventoryDetailLevel.SUMMARY
    include_names: bool = False
    names_limit: int = Field(default=100, ge=1, le=1000)
    include_document_chunks: bool = True


class MemoryInventorySummary(MemoryStats):
    pass


class ScopeDimensionInventory(MemoryModel):
    count: int = Field(default=0, ge=0)
    names: list[str] = Field(default_factory=list)
    truncated: bool = False
    remaining: int = Field(default=0, ge=0)


class ScopeInventory(MemoryModel):
    distinct_scope_tuples: int = Field(default=0, ge=0)
    global_records: int = Field(default=0, ge=0)
    scoped_records: int = Field(default=0, ge=0)
    user_ids: ScopeDimensionInventory = Field(default_factory=ScopeDimensionInventory)
    project_ids: ScopeDimensionInventory = Field(default_factory=ScopeDimensionInventory)
    agent_ids: ScopeDimensionInventory = Field(default_factory=ScopeDimensionInventory)


class MemoryTypeInventory(MemoryModel):
    memory_type: MemoryType
    display_name: str
    count: int = Field(default=0, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    newest_updated_at: datetime | None = None
    oldest_created_at: datetime | None = None


class MemoryInventoryNote(MemoryModel):
    code: str
    message: str


def _default_inventory_notes() -> list[MemoryInventoryNote]:
    return [
        MemoryInventoryNote(
            code="episodic_bucket_deferred",
            message=(
                "Episodic memory is treated as a derived reporting bucket over "
                "conversation_summary records and is not emitted until explicit "
                "metadata rules are implemented and tested."
            ),
        ),
        MemoryInventoryNote(
            code="scope_names_sensitive",
            message=(
                "Scope identity lists are sensitive operator data. They are emitted "
                "only when include_names=true, bounded by names_limit, and marked as "
                "truncated when capped."
            ),
        ),
        MemoryInventoryNote(
            code="inventory_fields_redacted",
            message=(
                "Inventory surfaces exclude raw record text, embeddings, and absolute "
                "file-system paths."
            ),
        ),
    ]


class MemoryInventoryReport(MemoryModel):
    contract_version: int = Field(default=1, ge=1)
    detail: InventoryDetailLevel = InventoryDetailLevel.SUMMARY
    generated_at: datetime = Field(default_factory=_utcnow)
    summary: MemoryInventorySummary = Field(default_factory=MemoryInventorySummary)
    scopes: ScopeInventory | None = None
    memory_types: list[MemoryTypeInventory] = Field(default_factory=list)
    notes: list[MemoryInventoryNote] = Field(default_factory=_default_inventory_notes)


class HealthStatus(MemoryModel):
    status: str
    database_path: Path
    schema_version: int = Field(ge=1)
    dependencies: dict[str, bool] = Field(default_factory=dict)
    message: str = ""


class ReadinessCheck(MemoryModel):
    name: str
    status: str
    code: str
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthLiveness(MemoryModel):
    contract_version: int = Field(default=1, ge=1)
    status: str = "alive"
    service: str = "bytebox"
    version: str
    trace_id: str


class HealthReadiness(MemoryModel):
    contract_version: int = Field(default=1, ge=1)
    status: str
    checks: list[ReadinessCheck] = Field(default_factory=list)
    trace_id: str


class HealthStatusReport(MemoryModel):
    contract_version: int = Field(default=1, ge=1)
    status: str
    service: str
    version: str
    build_commit: str | None = None
    build_time: datetime | None = None
    uptime_seconds: float = Field(default=0.0, ge=0.0)
    environment: str
    schema_version: int = Field(ge=1)
    database: dict[str, Any] = Field(default_factory=dict)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    tls: dict[str, Any] = Field(default_factory=dict)
    logging: dict[str, Any] = Field(default_factory=dict)
    jobs: dict[str, int] = Field(default_factory=dict)
    trace_id: str


class HealthStateReport(MemoryModel):
    contract_version: int = Field(default=1, ge=1)
    status: str
    trace_id: str
    counters: dict[str, int] = Field(default_factory=dict)
    memory_status_counts: dict[str, int] = Field(default_factory=dict)
    memory_type_counts: dict[str, int] = Field(default_factory=dict)
    queue: dict[str, Any] = Field(default_factory=dict)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    storage: dict[str, Any] = Field(default_factory=dict)
    recent_error: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)