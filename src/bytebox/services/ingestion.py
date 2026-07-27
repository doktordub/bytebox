"""Focused application service for document and folder ingestion."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..models import FolderIngestConnectionStrategy, FolderIngestResult, IngestResult, Scope
from .ingestion_document import DocumentIngestWorker
from .ingestion_folder import FolderIngestWorker


class DocumentIngestionService:
    """Facade over the document and folder ingestion workers."""

    def __init__(self, owner: Any) -> None:
        self._documents = DocumentIngestWorker(owner)
        self._folders = FolderIngestWorker(owner, self._documents)

    def ingest_document(
        self,
        path: str | Path,
        scope: Scope,
        *,
        dry_run: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_every_chunks: int = 0,
    ) -> IngestResult:
        return self._documents.ingest_document(
            path,
            scope,
            dry_run=dry_run,
            progress_callback=progress_callback,
            progress_every_chunks=progress_every_chunks,
        )

    def ingest_folder(
        self,
        path: str | Path,
        scope: Scope,
        *,
        stop_on_error: bool = False,
        continue_on_error: bool | None = None,
        resume_from: str | Path | None = None,
        connection_strategy: FolderIngestConnectionStrategy | str = (
            FolderIngestConnectionStrategy.REOPEN_ON_FAILURE
        ),
        dry_run: bool = False,
        manifest_path: str | Path | None = None,
        only_failed: bool = False,
        limit: int | None = None,
        since: datetime | str | None = None,
        progress_every_documents: int = 0,
        progress_every_chunks: int = 0,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FolderIngestResult:
        return self._folders.ingest_folder(
            path,
            scope,
            stop_on_error=stop_on_error,
            continue_on_error=continue_on_error,
            resume_from=resume_from,
            connection_strategy=connection_strategy,
            dry_run=dry_run,
            manifest_path=manifest_path,
            only_failed=only_failed,
            limit=limit,
            since=since,
            progress_every_documents=progress_every_documents,
            progress_every_chunks=progress_every_chunks,
            progress_callback=progress_callback,
        )