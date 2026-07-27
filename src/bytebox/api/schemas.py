"""REST request models and JSON serialization helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Self

from pydantic import Field, TypeAdapter, model_validator

from ..models import (
    FolderIngestConnectionStrategy,
    HealthStatus,
    ImportMode,
    MemoryCreate,
    MemoryExport,
    MemoryImport,
    MemoryModel,
    Scope,
)

_JSON_ADAPTER = TypeAdapter(Any)


class CreateMemoryRequest(MemoryModel):
    memory: MemoryCreate
    embed: bool = False


class IngestDocumentRequest(MemoryModel):
    path: Path
    scope: Scope
    dry_run: bool = False


class IngestFolderRequest(MemoryModel):
    path: Path
    scope: Scope
    stop_on_error: bool = False
    continue_on_error: bool | None = None
    resume_from: str | None = None
    dry_run: bool = False
    connection_strategy: FolderIngestConnectionStrategy = (
        FolderIngestConnectionStrategy.REOPEN_ON_FAILURE
    )
    only_failed: bool = False
    limit: int | None = Field(default=None, ge=1)
    since: datetime | None = None


class ChunkContextRequest(MemoryModel):
    user_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    before: int = 0
    after: int = 0

    @property
    def scope(self) -> Scope | None:
        if not any((self.user_id, self.project_id, self.agent_id)):
            return None
        return Scope(
            user_id=self.user_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
        )


class ExportMemoriesRequest(MemoryModel):
    scope: Scope | None = None
    user_id: str | None = None

    @model_validator(mode="after")
    def validate_export_target(self) -> Self:
        if self.scope is None and self.user_id is None:
            raise ValueError("Either scope or user_id must be provided.")

        if self.scope is not None and self.user_id is not None:
            if self.scope.user_id is not None and self.scope.user_id != self.user_id:
                raise ValueError("scope.user_id must match user_id when both are provided.")
            if self.scope.user_id is None:
                self.scope = self.scope.model_copy(update={"user_id": self.user_id})

        return self

    @property
    def resolved_scope(self) -> Scope:
        if self.scope is not None:
            return self.scope
        return Scope(user_id=self.user_id)

    def to_export(self, records: list[Any]) -> MemoryExport:
        return MemoryExport(scope=self.resolved_scope, records=records)


class ImportMemoriesRequest(MemoryModel):
    payload: MemoryImport
    mode: ImportMode = "upsert"


class DeleteByScopeRequest(MemoryModel):
    scope: Scope
    hard_delete: bool = False


class DeleteByScopeResponse(MemoryModel):
    deleted: int
    hard_delete: bool


def to_jsonable(value: Any) -> Any:
    return _JSON_ADAPTER.dump_python(value, mode="json")


def serialize_health_status(status: HealthStatus) -> dict[str, Any]:
    return to_jsonable(status)
