"""Administrative health and statistics models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from .base import MemoryModel


class MemoryStats(MemoryModel):
    total_records: int = Field(default=0, ge=0)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    type_counts: dict[str, int] = Field(default_factory=dict)


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