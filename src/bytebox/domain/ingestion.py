"""Ingestion result, manifest, and progress models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from .base import (
    FolderIngestConnectionStrategy,
    FolderIngestManifestStatus,
    IngestPhase,
    MemoryModel,
    _utcnow,
)


class IngestTimings(MemoryModel):
    parse_ms: int = Field(default=0, ge=0)
    chunk_ms: int = Field(default=0, ge=0)
    embed_ms: int = Field(default=0, ge=0)
    persist_ms: int = Field(default=0, ge=0)
    close_ms: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class IngestCounters(MemoryModel):
    section_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    insert_count: int = Field(default=0, ge=0)
    update_count: int = Field(default=0, ge=0)
    remove_count: int = Field(default=0, ge=0)


class IngestDiagnostics(MemoryModel):
    dry_run: bool = False
    file_size_bytes: int = Field(default=0, ge=0)
    frontmatter_bytes: int = Field(default=0, ge=0)
    frontmatter_keys: list[str] = Field(default_factory=list)
    dropped_metadata_fields: list[str] = Field(default_factory=list)
    limit_violations: list[str] = Field(default_factory=list)


class IngestResult(MemoryModel):
    path: Path
    ok: bool = True
    phase: IngestPhase = IngestPhase.COMPLETE
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    exception_class: str | None = None
    error: str | None = None
    exception_chain: list[str] = Field(default_factory=list)
    counters: IngestCounters = Field(default_factory=IngestCounters)
    diagnostics: IngestDiagnostics = Field(default_factory=IngestDiagnostics)
    timings: IngestTimings = Field(default_factory=IngestTimings)


class FolderIngestManifestEntry(MemoryModel):
    path: Path
    status: FolderIngestManifestStatus = FolderIngestManifestStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=_utcnow)
    file_size_bytes: int = Field(default=0, ge=0)
    modified_at: datetime | None = None
    error: str | None = None
    exception_class: str | None = None
    counters: IngestCounters = Field(default_factory=IngestCounters)
    timings: IngestTimings = Field(default_factory=IngestTimings)


class FolderIngestManifest(MemoryModel):
    root: Path
    updated_at: datetime = Field(default_factory=_utcnow)
    status_counts: dict[str, int] = Field(default_factory=dict)
    files: list[FolderIngestManifestEntry] = Field(default_factory=list)


class FolderIngestResult(MemoryModel):
    root: Path
    ok: bool = True
    connection_strategy: FolderIngestConnectionStrategy = (
        FolderIngestConnectionStrategy.REOPEN_ON_FAILURE
    )
    manifest_path: Path | None = None
    resume_from: str | None = None
    stop_on_error: bool = False
    stopped_on_error: bool = False
    only_failed: bool = False
    limit: int | None = Field(default=None, ge=1)
    since: datetime | None = None
    matched_files: int = Field(default=0, ge=0)
    skipped_files: int = Field(default=0, ge=0)
    files_processed: int = Field(default=0, ge=0)
    failed_files: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    files: list[IngestResult] = Field(default_factory=list)