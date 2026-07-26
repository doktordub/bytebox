from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memory_store.api.main import create_app
from memory_store.arcade import arcade_runtime_available
from memory_store.cli import main
from memory_store.errors import MemoryNotFoundError
from memory_store.models import (
    ImportResult,
    IngestResult,
    MemoryCreate,
    MemoryExport,
    MemoryFeedback,
    MemoryImport,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStats,
    MemoryUpdate,
    Scope,
)


def _record(**overrides: Any) -> MemoryRecord:
    now = datetime.now(timezone.utc)
    base = MemoryRecord(
        memory_id="mem-1",
        scope=Scope(project_id="arcade"),
        stable_key="decision:phase-10",
        title="Phase 10 adapter",
        summary="REST and CLI are thin wrappers.",
        text="REST and CLI delegate to MemoryStore.",
        created_at=now,
        updated_at=now,
    )
    return base.model_copy(update=overrides)


def _chunk_payload(
    *,
    chunk_id: str = "chunk-1",
    memory_id: str = "mem-1",
    text: str = "Deployment chunk text.",
    heading_path: list[str] | None = None,
    section_index: int = 1,
    section_chunk_index: int = 0,
    document_chunk_index: int = 1,
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "chunk_id": chunk_id,
        "source_path": "docs/architecture.md",
        "source_hash": f"hash-{chunk_id}",
        "title": "Architecture",
        "summary": "Chunk-first retrieval",
        "text": text,
        "tags": ["docs", "architecture"],
        "heading_path": heading_path or ["Architecture", "Deployment"],
        "section_index": section_index,
        "section_chunk_index": section_chunk_index,
        "document_chunk_index": document_chunk_index,
        "final_score": 0.8,
        "component_scores": {"retrieval_fusion": 0.8},
        "normalized_scores": {"retrieval_fusion": 0.8},
        "debug": {"route": "chunks"},
        "metadata": {"document_lifecycle": "source_controlled"},
    }


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._record = _record()
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def _call(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def add_memory(self, memory: MemoryCreate, *, embed: bool = False) -> MemoryRecord:
        self._call("add_memory", memory, embed=embed)
        return self._record.model_copy(update={"text": memory.text, "title": memory.title})

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        self._call("get_memory", memory_id)
        if memory_id == "missing":
            return None
        return self._record.model_copy(update={"memory_id": memory_id})

    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
        self._call("update_memory", memory_id, patch)
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        updated = {field: value for field, value in patch.model_dump(exclude_unset=True).items()}
        return self._record.model_copy(update={"memory_id": memory_id, **updated})

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        self._call("search", query)
        return [
            MemorySearchResult(
                memory=self._record,
                final_score=0.8,
                component_scores={"full_text": 0.8},
                normalized_scores={"full_text": 0.8},
                debug={"route": "search"},
            )
        ]

    def search_document_chunks(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._call("search_document_chunks", *args, **kwargs)
        return [_chunk_payload()]

    def get_chunk(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._call("get_chunk", *args, **kwargs)
        return _chunk_payload()

    def get_chunk_context(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._call("get_chunk_context", *args, **kwargs)
        return {
            "chunk": _chunk_payload(),
            "before": [
                _chunk_payload(
                    chunk_id="chunk-0",
                    memory_id="mem-0",
                    text="Overview chunk text.",
                    heading_path=["Architecture", "Overview"],
                    section_index=0,
                    section_chunk_index=0,
                    document_chunk_index=0,
                )
            ],
            "after": [
                _chunk_payload(
                    chunk_id="chunk-2",
                    memory_id="mem-2",
                    text="Rollout chunk text.",
                    heading_path=["Architecture", "Rollout"],
                    section_index=2,
                    section_chunk_index=0,
                    document_chunk_index=2,
                )
            ],
        }

    def ingest_document(self, path: str | Path, scope: Scope, **kwargs: Any) -> IngestResult:
        self._call("ingest_document", path, scope, **kwargs)
        return IngestResult(
            path=Path(path),
            added=1,
            diagnostics={"dry_run": bool(kwargs.get("dry_run", False))},
        )

    def ingest_folder(self, path: str | Path, scope: Scope, **kwargs: Any) -> Any:
        self._call("ingest_folder", path, scope, **kwargs)
        from memory_store.models import FolderIngestResult

        return FolderIngestResult(
            root=Path(path),
            files_processed=2,
            matched_files=2,
            failed_files=0,
            added=3,
            connection_strategy=kwargs.get("connection_strategy", "reopen_on_failure"),
            stop_on_error=bool(kwargs.get("stop_on_error", False)),
            manifest_path=kwargs.get("manifest_path"),
            resume_from=kwargs.get("resume_from"),
            only_failed=bool(kwargs.get("only_failed", False)),
            limit=kwargs.get("limit"),
            since=kwargs.get("since"),
        )

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
        self._call("add_feedback", memory_id, feedback)
        return self._record.model_copy(update={"memory_id": memory_id, "confidence": 0.9})

    def forget(self, memory_id: str) -> None:
        self._call("forget", memory_id)

    def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
        self._call("export_user_memories", user_id)
        return [self._record.model_copy(update={"scope": Scope(user_id=user_id)})]

    def export_scope(self, scope: Scope) -> MemoryExport:
        self._call("export_scope", scope)
        return MemoryExport(scope=scope, records=[self._record.model_copy(update={"scope": scope})])

    def import_memories(self, payload: MemoryImport, mode: str = "upsert") -> ImportResult:
        self._call("import_memories", payload, mode=mode)
        return ImportResult(inserted=len(payload.records))

    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
        self._call("delete_by_scope", scope, hard_delete=hard_delete)
        return 2

    def health(self) -> Any:
        self._call("health")
        from memory_store.models import HealthStatus

        return HealthStatus(
            status="ok",
            database_path=Path("./fake-db"),
            schema_version=1,
            dependencies={"arcadedb_embedded": True, "fastembed": True},
            message="ready",
        )

    def stats(self) -> MemoryStats:
        self._call("stats")
        return MemoryStats(
            total_records=3,
            scope_counts={"global": 1, "scoped": 2},
            status_counts={"active": 3},
            type_counts={"decision": 1, "observation": 2},
        )


def test_rest_routes_delegate_to_store_and_enforce_local_token() -> None:
    store = FakeStore()
    app = create_app(store=store, api={"local_api_token": "secret-token"})

    with TestClient(app) as client:
        unauthorized = client.get("/health")
        assert unauthorized.status_code == 401

        headers = {"X-API-Token": "secret-token"}

        create_response = client.post(
            "/memories",
            headers=headers,
            json={
                "memory": {
                    "scope": {"project_id": "arcade"},
                    "title": "Adapter note",
                    "text": "Adapters should stay thin.",
                },
                "embed": False,
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["text"] == "Adapters should stay thin."

        get_response = client.get("/memories/mem-123", headers=headers)
        assert get_response.status_code == 200
        assert get_response.json()["memory_id"] == "mem-123"

        missing_response = client.get("/memories/missing", headers=headers)
        assert missing_response.status_code == 404

        patch_response = client.patch(
            "/memories/mem-123",
            headers=headers,
            json={"summary": "Updated through REST"},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["summary"] == "Updated through REST"

        search_response = client.post(
            "/memories/search",
            headers=headers,
            json={"scope": {"project_id": "arcade"}, "text": "thin adapters"},
        )
        assert search_response.status_code == 200
        assert search_response.json()[0]["debug"]["route"] == "search"

        ingest_response = client.post(
            "/documents/ingest",
            headers=headers,
            json={
                "path": "docs/architecture.md",
                "scope": {"project_id": "arcade"},
                "dry_run": True,
            },
        )
        assert ingest_response.status_code == 200
        assert ingest_response.json()["added"] == 1
        assert ingest_response.json()["diagnostics"]["dry_run"] is True

        folder_response = client.post(
            "/documents/ingest-folder",
            headers=headers,
            json={
                "path": "docs",
                "scope": {"project_id": "arcade"},
                "continue_on_error": True,
                "resume_from": "b.md",
                "connection_strategy": "shared_store",
                "manifest_path": "docs/.memory_store_ingest_manifest.json",
                "only_failed": True,
                "limit": 2,
                "since": "2026-07-01T00:00:00+00:00",
            },
        )
        assert folder_response.status_code == 200
        assert folder_response.json()["files_processed"] == 2
        assert folder_response.json()["connection_strategy"] == "shared_store"
        assert folder_response.json()["resume_from"] == "b.md"
        assert folder_response.json()["only_failed"] is True
        assert folder_response.json()["limit"] == 2

        feedback_response = client.post(
            "/memories/mem-123/feedback",
            headers=headers,
            json={"positive": True},
        )
        assert feedback_response.status_code == 200
        assert feedback_response.json()["confidence"] == 0.9

        forget_response = client.post("/memories/mem-123/forget", headers=headers)
        assert forget_response.status_code == 200
        assert forget_response.json()["memory_id"] == "mem-123"

        export_response = client.post(
            "/memories/export",
            headers=headers,
            json={"user_id": "user-1"},
        )
        assert export_response.status_code == 200
        assert export_response.json()["scope"] == {
            "user_id": "user-1",
            "project_id": None,
            "agent_id": None,
        }

        import_response = client.post(
            "/memories/import",
            headers=headers,
            json={
                "payload": {"records": [_record().model_dump(mode="json")], "source": "api"},
                "mode": "upsert",
            },
        )
        assert import_response.status_code == 200
        assert import_response.json()["inserted"] == 1

        delete_response = client.post(
            "/memories/delete-by-scope",
            headers=headers,
            json={"scope": {"project_id": "arcade"}, "hard_delete": True},
        )
        assert delete_response.status_code == 200
        assert delete_response.json() == {"deleted": 2, "hard_delete": True}

        health_response = client.get("/health", headers=headers)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        stats_response = client.get("/stats", headers=headers)
        assert stats_response.status_code == 200
        assert stats_response.json()["total_records"] == 3

    called_methods = [name for name, _args, _kwargs in store.calls]
    assert called_methods == [
        "add_memory",
        "get_memory",
        "get_memory",
        "update_memory",
        "search",
        "ingest_document",
        "ingest_folder",
        "add_feedback",
        "forget",
        "get_memory",
        "export_user_memories",
        "import_memories",
        "delete_by_scope",
        "health",
        "stats",
    ]
    ingest_call = store.calls[5]
    assert ingest_call[2] == {"dry_run": True}
    folder_call = store.calls[6]
    assert folder_call[2] == {
        "stop_on_error": False,
        "continue_on_error": True,
        "resume_from": "b.md",
        "connection_strategy": "shared_store",
        "manifest_path": Path("docs/.memory_store_ingest_manifest.json"),
        "only_failed": True,
        "limit": 2,
        "since": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    }
    assert store.closed is True


def test_rest_chunk_routes_define_chunk_specific_contract() -> None:
    store = FakeStore()
    app = create_app(store=store, api={"local_api_token": "secret-token"})

    with TestClient(app) as client:
        headers = {"X-API-Token": "secret-token"}

        search_response = client.post(
            "/chunks/search",
            headers=headers,
            json={
                "scope": {"project_id": "arcade"},
                "text": "deployment architecture",
                "before": 1,
                "after": 1,
                "limit": 3,
            },
        )
        assert search_response.status_code == 200
        assert search_response.json()[0]["chunk_id"] == "chunk-1"
        assert search_response.json()[0]["memory_id"] == "mem-1"
        assert search_response.json()[0]["section_index"] == 1
        assert search_response.json()[0]["section_chunk_index"] == 0
        assert search_response.json()[0]["document_chunk_index"] == 1
        assert search_response.json()[0]["debug"]["route"] == "chunks"

        get_response = client.get("/chunks/chunk-1", headers=headers)
        assert get_response.status_code == 200
        assert get_response.json()["chunk_id"] == "chunk-1"

        context_response = client.get(
            "/chunks/chunk-1/context",
            headers=headers,
            params={"project_id": "arcade", "before": 1, "after": 1},
        )
        assert context_response.status_code == 200
        assert context_response.json()["chunk"]["chunk_id"] == "chunk-1"
        assert [item["chunk_id"] for item in context_response.json()["before"]] == ["chunk-0"]
        assert [item["chunk_id"] for item in context_response.json()["after"]] == ["chunk-2"]

    called_methods = [name for name, _args, _kwargs in store.calls]
    assert called_methods[:3] == ["search_document_chunks", "get_chunk", "get_chunk_context"]


@pytest.mark.skipif(not arcade_runtime_available(), reason="arcadedb_embedded is required")
def test_rest_app_smoke_flows_against_real_store(tmp_path) -> None:
    app = create_app(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    with TestClient(app) as client:
        create_response = client.post(
            "/memories",
            json={
                "memory": {
                    "scope": {"project_id": "arcade"},
                    "stable_key": "decision:rest-smoke",
                    "title": "REST smoke",
                    "text": "REST routes should reuse the service layer.",
                }
            },
        )
        assert create_response.status_code == 200
        memory_id = create_response.json()["memory_id"]

        get_response = client.get(f"/memories/{memory_id}")
        assert get_response.status_code == 200
        assert get_response.json()["title"] == "REST smoke"

        search_response = client.post(
            "/memories/search",
            json={"scope": {"project_id": "arcade"}, "text": "service layer"},
        )
        assert search_response.status_code == 200
        assert search_response.json()[0]["memory"]["memory_id"] == memory_id

        stats_response = client.get("/stats")
        assert stats_response.status_code == 200
        assert stats_response.json()["total_records"] == 1


def test_cli_commands_delegate_to_store_and_eval_runner(monkeypatch, capsys, tmp_path) -> None:
    store = FakeStore()

    monkeypatch.setattr(
        "memory_store.cli.MemoryStore.from_config",
        lambda config_path=None, **_overrides: store,
    )
    monkeypatch.setattr(
        "memory_store.cli.run_evaluation",
        lambda path=None, config_path=None: f"Eval scaffold ready. Queries file: {path}",
    )

    export_path = tmp_path / "memories.json"
    report_path = tmp_path / "ingest-report.json"
    manifest_path = tmp_path / "ingest-manifest.json"

    assert main(["init"]) == 0
    init_output = json.loads(capsys.readouterr().out)
    assert init_output["status"] == "ok"

    assert main(["ingest-file", "docs/architecture.md", "--project-id", "arcade", "--dry-run"]) == 0
    ingest_output = json.loads(capsys.readouterr().out)
    assert ingest_output["added"] == 1
    assert ingest_output["diagnostics"]["dry_run"] is True

    assert (
        main(
            [
                "ingest-folder",
                "docs",
                "--project-id",
                "arcade",
                "--continue-on-error",
                "--resume-from",
                "b.md",
                "--connection-strategy",
                "shared_store",
                "--manifest-path",
                str(manifest_path),
                "--only-failed",
                "--limit",
                "2",
                "--since",
                "2026-07-01T00:00:00+00:00",
                "--report-path",
                str(report_path),
            ]
        )
        == 0
    )
    folder_output = json.loads(capsys.readouterr().out)
    assert folder_output["connection_strategy"] == "shared_store"
    assert json.loads(report_path.read_text(encoding="utf-8"))["resume_from"] == "b.md"

    assert main(["search", "thin adapters", "--project-id", "arcade"]) == 0
    search_output = json.loads(capsys.readouterr().out)
    assert search_output[0]["debug"]["route"] == "search"

    assert main(["search-chunks", "deployment architecture", "--project-id", "arcade"]) == 0
    chunk_search_output = json.loads(capsys.readouterr().out)
    assert chunk_search_output[0]["chunk_id"] == "chunk-1"

    assert (
        main(
            ["chunk-context", "chunk-1", "--project-id", "arcade", "--before", "1", "--after", "1"]
        )
        == 0
    )
    chunk_context_output = json.loads(capsys.readouterr().out)
    assert chunk_context_output["chunk"]["chunk_id"] == "chunk-1"
    assert [item["chunk_id"] for item in chunk_context_output["before"]] == ["chunk-0"]
    assert [item["chunk_id"] for item in chunk_context_output["after"]] == ["chunk-2"]

    assert main(["export", "--user-id", "user-1", "--out", str(export_path)]) == 0
    export_output = json.loads(capsys.readouterr().out)
    assert export_output["records"]
    written_export = json.loads(export_path.read_text(encoding="utf-8"))
    assert written_export["scope"]["user_id"] == "user-1"

    assert main(["delete-by-scope", "--project-id", "arcade", "--dry-run"]) == 0
    delete_output = json.loads(capsys.readouterr().out)
    assert delete_output == {
        "count": 1,
        "dry_run": True,
        "hard_delete": False,
        "scope": {"user_id": None, "project_id": "arcade", "agent_id": None},
    }

    assert main(["eval", "evals/golden_queries.yaml"]) == 0
    eval_output = capsys.readouterr().out.strip()
    assert "evals/golden_queries.yaml" in eval_output

    called_methods = [name for name, _args, _kwargs in store.calls]
    assert called_methods == [
        "health",
        "ingest_document",
        "ingest_folder",
        "search",
        "search_document_chunks",
        "get_chunk_context",
        "export_user_memories",
        "export_scope",
    ]
    ingest_call = store.calls[1]
    assert ingest_call[2] == {"dry_run": True}
    folder_call = store.calls[2]
    assert folder_call[2] == {
        "stop_on_error": False,
        "continue_on_error": True,
        "resume_from": "b.md",
        "connection_strategy": "shared_store",
        "manifest_path": manifest_path,
        "only_failed": True,
        "limit": 2,
        "since": "2026-07-01T00:00:00+00:00",
    }


def test_cli_unlock_delegates_to_arcade_unlock(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(
        "memory_store.cli.load_settings",
        lambda config_path=None: type("Settings", (), {"database": type("DB", (), {"path": tmp_path / "arcade"})()})(),
    )
    monkeypatch.setattr(
        "memory_store.cli.unlock_arcade_database",
        lambda database_path, force=False: {
            "lock_path": str(Path(database_path).with_name(f"{Path(database_path).name}.lock")),
            "removed": True,
            "force": force,
            "stale_owner": True,
            "owner": {"pid": 0},
        },
    )

    assert main(["unlock", "--force"]) == 0
    unlock_output = json.loads(capsys.readouterr().out)
    assert unlock_output["removed"] is True
    assert unlock_output["force"] is True
