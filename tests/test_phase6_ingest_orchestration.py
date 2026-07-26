from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from memory_store.config import MemoryStoreSettings
from memory_store.errors import IngestionError, PersistenceError
from memory_store.models import (
    FolderIngestConnectionStrategy,
    IngestCounters,
    IngestPhase,
    IngestResult,
    IngestTimings,
    Scope,
)
from memory_store.service import MemoryService


def _make_service(tmp_path: Path) -> MemoryService:
    service = MemoryService.__new__(MemoryService)
    service.settings = MemoryStoreSettings(database={"path": tmp_path / "arcade"})
    service._database_handle = None
    service._repository_instance = None
    service._embedding_provider_instance = None
    service._lifecycle_manager_instance = None
    service._privacy_controller_instance = None
    service._active_embedding_dimension = None
    service._schema_version = service.settings.database.schema_version
    return service


def _annotated_failure(
    service: MemoryService,
    *,
    path: Path,
    phase: IngestPhase,
    exc: Exception,
    counters: IngestCounters | None = None,
    timings: IngestTimings | None = None,
) -> PersistenceError | IngestionError:
    error: PersistenceError | IngestionError
    if phase is IngestPhase.PERSIST:
        error = PersistenceError(f"synthetic {phase.value} failure")
    else:
        error = IngestionError(f"synthetic {phase.value} failure")
    return service._annotate_ingestion_exception(
        error,
        path=path,
        phase=phase,
        counters=counters or IngestCounters(),
        timings=timings or IngestTimings(),
        root_exc=exc,
    )


def test_ingest_folder_reopen_on_failure_records_exception_chain_and_continues(
    monkeypatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A", encoding="utf-8")
    (docs / "b.md").write_text("# B", encoding="utf-8")

    service = _make_service(tmp_path)
    close_calls = 0
    verify_calls = 0
    ensure_calls = 0

    def fake_close() -> None:
        nonlocal close_calls
        close_calls += 1

    def fake_verify() -> None:
        nonlocal verify_calls
        verify_calls += 1

    def fake_ensure() -> None:
        nonlocal ensure_calls
        ensure_calls += 1

    def fake_ingest_document(path: str | Path, scope: Scope) -> IngestResult:
        del scope
        resolved = Path(path)
        if resolved.name == "a.md":
            try:
                raise RuntimeError("inner boom")
            except RuntimeError as exc:
                raise _annotated_failure(
                    service,
                    path=resolved,
                    phase=IngestPhase.PERSIST,
                    exc=exc,
                    counters=IngestCounters(
                        section_count=1,
                        chunk_count=2,
                        insert_count=2,
                    ),
                    timings=IngestTimings(
                        parse_ms=1,
                        chunk_ms=2,
                        embed_ms=3,
                        persist_ms=4,
                        elapsed_ms=10,
                    ),
                ) from exc

        return IngestResult(
            path=resolved,
            added=1,
            counters=IngestCounters(section_count=1, chunk_count=1, insert_count=1),
            timings=IngestTimings(parse_ms=1, chunk_ms=1, embed_ms=1, persist_ms=1, elapsed_ms=4),
        )

    monkeypatch.setattr(service, "close", fake_close)
    monkeypatch.setattr(service, "_verify_lock_released", fake_verify)
    monkeypatch.setattr(service, "_ensure_repository", fake_ensure)
    monkeypatch.setattr(service, "ingest_document", fake_ingest_document)

    result = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        connection_strategy=FolderIngestConnectionStrategy.REOPEN_ON_FAILURE,
    )

    assert result.ok is False
    assert result.matched_files == 2
    assert result.files_processed == 2
    assert result.failed_files == 1
    assert result.added == 1
    assert [item.path.as_posix() for item in result.files] == ["a.md", "b.md"]
    assert result.files[0].ok is False
    assert result.files[0].phase is IngestPhase.PERSIST
    assert result.files[0].exception_class == "PersistenceError"
    assert result.files[0].exception_chain == [
        "PersistenceError: synthetic persist failure",
        "RuntimeError: inner boom",
    ]
    assert result.files[0].counters.chunk_count == 2
    assert close_calls == 1
    assert verify_calls == 1
    assert ensure_calls == 1
    assert result.files[1].ok is True


def test_ingest_folder_resume_from_and_stop_on_error(monkeypatch, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A", encoding="utf-8")
    (docs / "b.md").write_text("# B", encoding="utf-8")
    (docs / "c.md").write_text("# C", encoding="utf-8")

    service = _make_service(tmp_path)
    seen_paths: list[str] = []

    def fake_ingest_document(path: str | Path, scope: Scope) -> IngestResult:
        del scope
        resolved = Path(path)
        seen_paths.append(resolved.name)
        try:
            raise ValueError("bad frontmatter")
        except ValueError as exc:
            raise _annotated_failure(
                service,
                path=resolved,
                phase=IngestPhase.PARSE,
                exc=exc,
            ) from exc

    monkeypatch.setattr(service, "ingest_document", fake_ingest_document)

    result = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        resume_from="b.md",
        stop_on_error=True,
        connection_strategy=FolderIngestConnectionStrategy.SHARED_STORE,
    )

    assert seen_paths == ["b.md"]
    assert result.ok is False
    assert result.resume_from == "b.md"
    assert result.matched_files == 2
    assert result.files_processed == 1
    assert result.failed_files == 1
    assert result.stopped_on_error is True
    assert [item.path.as_posix() for item in result.files] == ["b.md"]


def test_ingest_folder_reopen_per_file_closes_after_each_success(monkeypatch, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A", encoding="utf-8")
    (docs / "b.md").write_text("# B", encoding="utf-8")

    service = _make_service(tmp_path)
    close_calls = 0
    verify_calls = 0

    def fake_close() -> None:
        nonlocal close_calls
        close_calls += 1

    def fake_verify() -> None:
        nonlocal verify_calls
        verify_calls += 1

    def fake_ingest_document(path: str | Path, scope: Scope) -> IngestResult:
        del scope
        return IngestResult(path=Path(path), added=1, timings=IngestTimings(elapsed_ms=1))

    monkeypatch.setattr(service, "close", fake_close)
    monkeypatch.setattr(service, "_verify_lock_released", fake_verify)
    monkeypatch.setattr(service, "ingest_document", fake_ingest_document)

    result = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        connection_strategy=FolderIngestConnectionStrategy.REOPEN_PER_FILE,
    )

    assert result.ok is True
    assert result.files_processed == 2
    assert result.failed_files == 0
    assert close_calls == 2
    assert verify_calls == 2
    assert all(item.timings.close_ms >= 0 for item in result.files)


def test_ingest_folder_persists_manifest_and_retries_only_failed_documents(
    monkeypatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A", encoding="utf-8")
    (docs / "b.md").write_text("# B", encoding="utf-8")
    (docs / "c.md").write_text("# C", encoding="utf-8")

    service = _make_service(tmp_path)
    manifest_path = tmp_path / "ingest-manifest.json"
    seen_paths: list[str] = []
    attempts: dict[str, int] = {}

    def fake_ingest_document(path: str | Path, scope: Scope, **kwargs: Any) -> IngestResult:
        del scope, kwargs
        resolved = Path(path)
        seen_paths.append(resolved.name)
        attempts[resolved.name] = attempts.get(resolved.name, 0) + 1

        if resolved.name == "b.md" and attempts[resolved.name] == 1:
            try:
                raise RuntimeError("persist exploded")
            except RuntimeError as exc:
                raise _annotated_failure(
                    service,
                    path=resolved,
                    phase=IngestPhase.PERSIST,
                    exc=exc,
                    counters=IngestCounters(chunk_count=3),
                    timings=IngestTimings(elapsed_ms=5),
                ) from exc

        return IngestResult(
            path=resolved,
            added=1,
            counters=IngestCounters(chunk_count=1, insert_count=1),
            timings=IngestTimings(elapsed_ms=1),
        )

    monkeypatch.setattr(service, "ingest_document", fake_ingest_document)

    first = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        manifest_path=manifest_path,
        connection_strategy=FolderIngestConnectionStrategy.SHARED_STORE,
    )

    assert first.ok is False
    assert first.failed_files == 1
    assert first.status_counts == {"failed": 1, "succeeded": 2}
    assert seen_paths == ["a.md", "b.md", "c.md"]

    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_statuses = {item["path"]: item["status"] for item in first_manifest["files"]}
    assert first_statuses == {
        "a.md": "succeeded",
        "b.md": "failed",
        "c.md": "succeeded",
    }

    seen_paths.clear()
    second = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        manifest_path=manifest_path,
        only_failed=True,
        connection_strategy=FolderIngestConnectionStrategy.SHARED_STORE,
    )

    assert second.ok is True
    assert second.matched_files == 1
    assert second.skipped_files == 2
    assert second.failed_files == 0
    assert second.status_counts == {"succeeded": 3}
    assert seen_paths == ["b.md"]

    second_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    second_statuses = {item["path"]: item["status"] for item in second_manifest["files"]}
    assert second_statuses == {
        "a.md": "succeeded",
        "b.md": "succeeded",
        "c.md": "succeeded",
    }


def test_ingest_folder_applies_since_limit_and_emits_progress(monkeypatch, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    old_file = docs / "a.md"
    first_new_file = docs / "b.md"
    second_new_file = docs / "c.md"
    old_file.write_text("# A", encoding="utf-8")
    first_new_file.write_text("# B", encoding="utf-8")
    second_new_file.write_text("# C", encoding="utf-8")

    old_timestamp = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    new_timestamp = datetime(2026, 7, 2, tzinfo=timezone.utc).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))
    os.utime(first_new_file, (new_timestamp, new_timestamp))
    os.utime(second_new_file, (new_timestamp, new_timestamp))

    service = _make_service(tmp_path)
    progress_events: list[dict[str, Any]] = []
    seen_paths: list[str] = []

    def fake_ingest_document(path: str | Path, scope: Scope, **kwargs: Any) -> IngestResult:
        del scope
        resolved = Path(path)
        seen_paths.append(resolved.name)
        assert kwargs["progress_every_chunks"] == 2
        progress_callback = kwargs["progress_callback"]
        progress_callback(
            {
                "kind": "chunk",
                "phase": "embed",
                "path": resolved.as_posix(),
                "completed_chunks": 2,
                "total_chunks": 4,
                "document_chunks": 4,
            }
        )
        progress_callback(
            {
                "kind": "chunk",
                "phase": "embed",
                "path": resolved.as_posix(),
                "completed_chunks": 4,
                "total_chunks": 4,
                "document_chunks": 4,
            }
        )
        return IngestResult(
            path=resolved,
            added=1,
            counters=IngestCounters(chunk_count=4, insert_count=1),
            timings=IngestTimings(elapsed_ms=2),
        )

    monkeypatch.setattr(service, "ingest_document", fake_ingest_document)

    result = service.ingest_folder(
        docs,
        Scope(project_id="docs"),
        since=datetime.fromtimestamp(new_timestamp, tz=timezone.utc) - timedelta(minutes=1),
        limit=1,
        manifest_path=tmp_path / "progress-manifest.json",
        progress_every_documents=1,
        progress_every_chunks=2,
        progress_callback=progress_events.append,
        connection_strategy=FolderIngestConnectionStrategy.SHARED_STORE,
    )

    assert result.ok is True
    assert result.matched_files == 1
    assert result.skipped_files == 1
    assert seen_paths == ["b.md"]
    assert [event["kind"] for event in progress_events] == ["chunk", "chunk", "document"]
    assert progress_events[0]["path"] == "b.md"
    assert progress_events[1]["completed_chunks"] == 4
    assert progress_events[2] == {
        "kind": "document",
        "path": "b.md",
        "processed_files": 1,
        "total_files": 1,
        "failed_files": 0,
        "ok": True,
    }