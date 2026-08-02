from __future__ import annotations

import json
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store.api.main import create_app as create_docs_app
from memory_store.api.main import create_inprocess_app as create_app
from memory_store.arcade import arcade_runtime_available
from memory_store.cli import main
from memory_store.embeddings.fastembed_provider import fastembed_runtime_available
from memory_store.errors import MemoryNotFoundError
from memory_store.models import (
    ImportResult,
    InventoryDetailLevel,
    IngestResult,
    MemoryInventoryQuery,
    MemoryInventoryReport,
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

from bytebox.api.security import build_api_tls_context
from bytebox.config import ByteBoxSettings


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
            total_records=4,
            scope_counts={"global": 1, "scoped": 3},
            status_counts={"active": 4},
            type_counts={"decision": 1, "document_chunk": 1, "observation": 2},
        )

    def inventory(
        self,
        *,
        detail: InventoryDetailLevel | str = InventoryDetailLevel.SUMMARY,
        include_names: bool = False,
        names_limit: int = 100,
        include_document_chunks: bool = True,
    ) -> MemoryInventoryReport:
        self._call(
            "inventory",
            detail=detail,
            include_names=include_names,
            names_limit=names_limit,
            include_document_chunks=include_document_chunks,
        )
        detail_level = InventoryDetailLevel(detail)
        type_counts = {"decision": 1, "observation": 2}
        if include_document_chunks:
            type_counts["document_chunk"] = 1
        total_records = sum(type_counts.values())
        report = {
            "detail": detail_level,
            "summary": {
                "total_records": total_records,
                "scope_counts": {"global": 1, "scoped": total_records - 1},
                "status_counts": {"active": total_records},
                "type_counts": type_counts,
            },
            "scopes": None,
            "memory_types": [],
        }
        if detail_level == InventoryDetailLevel.FULL:
            names = ["arcade"] if include_names else []
            report["scopes"] = {
                "distinct_scope_tuples": 2,
                "global_records": 1,
                "scoped_records": total_records - 1,
                "user_ids": {
                    "count": 1,
                    "names": names[:names_limit],
                    "truncated": False,
                    "remaining": 0,
                },
                "project_ids": {
                    "count": 1,
                    "names": names[:names_limit],
                    "truncated": False,
                    "remaining": 0,
                },
                "agent_ids": {
                    "count": 0,
                    "names": [],
                    "truncated": False,
                    "remaining": 0,
                },
            }
            report["memory_types"] = [
                {
                    "memory_type": memory_type,
                    "display_name": memory_type.replace("_", " ").title(),
                    "count": count,
                    "status_counts": {"active": count},
                    "scope_counts": {"global": 1 if memory_type == "observation" else 0, "scoped": count - (1 if memory_type == "observation" else 0)},
                    "oldest_created_at": "2026-07-01T00:00:00Z",
                    "newest_updated_at": "2026-07-02T00:00:00Z",
                }
                for memory_type, count in sorted(type_counts.items())
            ]
        return MemoryInventoryReport.model_validate(report)


def test_rest_routes_delegate_to_store_and_enforce_local_token() -> None:
    store = FakeStore()
    app = create_app(
        store=store,
        api={"local_api_token": "secret-token"},
        security={"hard_delete_enabled": True},
    )

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
        assert import_response.status_code == 422

        import_response = client.post(
            "/memories/import",
            headers={**headers, "X-Idempotency-Key": "import-1"},
            json={
                "payload": {"records": [_record().model_dump(mode="json")], "source": "api"},
                "mode": "upsert",
            },
        )
        assert import_response.status_code == 200
        assert import_response.json()["inserted"] == 1

        delete_response = client.post(
            "/memories/delete-by-scope",
            headers={**headers, "X-Confirm-Delete": "hard-delete"},
            json={"scope": {"project_id": "arcade"}, "hard_delete": True},
        )
        assert delete_response.status_code == 200
        assert delete_response.json() == {"deleted": 2, "hard_delete": True}

        health_response = client.get("/health", headers=headers)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        stats_response = client.get("/stats", headers=headers)
        assert stats_response.status_code == 200
        assert stats_response.json()["total_records"] == 4

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
        "only_failed": True,
        "limit": 2,
        "since": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    }
    assert store.closed is True


def test_inventory_contract_and_openapi_metadata_are_explicit() -> None:
    query = MemoryInventoryQuery()
    report = MemoryInventoryReport()

    assert query.detail == InventoryDetailLevel.SUMMARY
    assert query.include_names is False
    assert query.names_limit == 100
    assert query.include_document_chunks is True
    assert report.detail == InventoryDetailLevel.SUMMARY
    assert report.generated_at.tzinfo is not None
    assert report.summary.total_records == 0
    note_codes = {note.code for note in report.notes}
    assert note_codes >= {
        "episodic_bucket_deferred",
        "scope_names_sensitive",
        "inventory_fields_redacted",
    }
    assert "conversation_summary" in report.notes[0].message

    app = create_docs_app(store=FakeStore(), api={"local_api_token": "secret-token"})

    with TestClient(app) as client:
        openapi_response = client.get("/openapi.json")
        assert openapi_response.status_code == 200
        payload = openapi_response.json()

        assert payload["paths"]["/status"]["get"]["summary"] == "Runtime status summary"
        assert payload["paths"]["/state"]["get"]["summary"] == "Operational state snapshot"
        assert payload["paths"]["/metrics"]["get"]["summary"] == "OpenMetrics scrape payload"
        assert payload["paths"]["/stats"]["get"]["summary"] == "Compact database summary"

        inventory_operation = payload["paths"]["/inventory"]["get"]
        assert inventory_operation["summary"] == "Database inventory contract"
        assert "Return the detailed database inventory" in inventory_operation["description"]
        assert "scope names are sensitive" in inventory_operation["description"]
        assert "Raw record text, embeddings, and absolute file-system paths are excluded" in inventory_operation["description"]

        parameters = {item["name"]: item for item in inventory_operation["parameters"]}
        for name in ("detail", "include_names", "names_limit", "include_document_chunks"):
            assert name in parameters
        assert parameters["detail"]["schema"]["default"] == "summary"
        assert parameters["names_limit"]["schema"]["default"] == 100

        inventory_response = client.get(
            "/inventory",
            headers={"X-API-Token": "secret-token"},
            params={"detail": "full", "include_names": True, "names_limit": 1},
        )
        assert inventory_response.status_code == 200
        inventory_payload = inventory_response.json()
        assert inventory_payload["detail"] == "full"
        assert inventory_payload["generated_at"]
        assert inventory_payload["summary"]["total_records"] == 4
        assert inventory_payload["scopes"]["project_ids"]["names"] == ["arcade"]
        assert {item["memory_type"] for item in inventory_payload["memory_types"]} == {
            "decision",
            "document_chunk",
            "observation",
        }
        assert {item["code"] for item in inventory_payload["notes"]} >= {
            "episodic_bucket_deferred",
            "scope_names_sensitive",
            "inventory_fields_redacted",
        }

        serialized = json.dumps(inventory_payload)
        assert _record().text not in serialized
        assert _record().summary not in serialized
        assert "./fake-db" not in serialized
        assert '"source_path"' not in serialized
        assert '"embedding_model"' not in serialized


def test_state_and_stats_share_the_inventory_summary_surface() -> None:
    store = FakeStore()
    app = create_app(
        store=store,
        api={
            "local_api_token": "secret-token",
            "local_api_token_scopes": ["admin:read", "admin:operate"],
        },
        database={"path": "./fake-db"},
    )

    with TestClient(app) as client:
        headers = {"X-API-Token": "secret-token"}

        stats_response = client.get("/stats", headers=headers)
        assert stats_response.status_code == 200

        inventory_response = client.get(
            "/inventory",
            headers=headers,
            params={
                "detail": "full",
                "include_names": True,
                "names_limit": 1,
                "include_document_chunks": False,
            },
        )
        assert inventory_response.status_code == 200
        assert inventory_response.json()["summary"]["type_counts"] == {
            "decision": 1,
            "observation": 2,
        }

        state_response = client.get("/state", headers=headers)
        assert state_response.status_code == 200
        state_payload = state_response.json()

    assert state_payload["counters"] == {
        "total_records": stats_response.json()["total_records"],
        "global_records": stats_response.json()["scope_counts"]["global"],
        "scoped_records": stats_response.json()["scope_counts"]["scoped"],
    }
    assert state_payload["memory_status_counts"] == stats_response.json()["status_counts"]
    assert state_payload["memory_type_counts"] == stats_response.json()["type_counts"]
    inventory_calls = [kwargs for name, _args, kwargs in store.calls if name == "inventory"]
    assert inventory_calls == [
        {
            "detail": InventoryDetailLevel.FULL,
            "include_names": True,
            "names_limit": 1,
            "include_document_chunks": False,
        },
        {
            "detail": InventoryDetailLevel.SUMMARY,
            "include_names": False,
            "names_limit": 100,
            "include_document_chunks": True,
        },
    ]


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
    from memory_store.api.main import create_app as create_rest_shim_app

    app = create_rest_shim_app(
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


def test_rest_routes_return_sanitized_error_envelopes_and_enforce_scopes() -> None:
    store = FakeStore()
    app = create_app(
        store=store,
        api={
            "auth": {
                "tokens": [
                    {
                        "name": "reader",
                        "token": "reader-token",
                        "scopes": ["memory:read", "admin:read"],
                    },
                    {
                        "name": "writer",
                        "token": "writer-token",
                        "scopes": ["memory:write"],
                    },
                ]
            }
        },
    )

    with TestClient(app) as client:
        forbidden = client.post(
            "/memories",
            headers={"X-API-Token": "reader-token"},
            json={
                "memory": {
                    "scope": {"project_id": "arcade"},
                    "title": "Adapter note",
                    "text": "Adapters should stay thin.",
                },
                "embed": False,
            },
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "BYTEBOX_FORBIDDEN"
        assert "Adapters should stay thin" not in json.dumps(forbidden.json())

        missing = client.get("/memories/missing", headers={"X-API-Token": "reader-token"})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "BYTEBOX_RESOURCE_NOT_FOUND"
        assert "missing" not in json.dumps(missing.json())
        assert missing.headers["X-Trace-ID"]


def test_rest_ingest_folder_rejects_manifest_path_input() -> None:
    store = FakeStore()
    app = create_app(store=store, api={"local_api_token": "secret-token"})

    with TestClient(app) as client:
        response = client.post(
            "/documents/ingest-folder",
            headers={"X-API-Token": "secret-token"},
            json={
                "path": "docs",
                "scope": {"project_id": "arcade"},
                "manifest_path": "docs/.bytebox_ingest_manifest.json",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BYTEBOX_INVALID_REQUEST"


def test_request_body_limit_returns_sanitized_413() -> None:
    store = FakeStore()
    app = create_app(
        store=store,
        api={
            "local_api_token": "secret-token",
            "max_request_body_bytes": 32,
        },
    )

    with TestClient(app) as client:
        response = client.post(
            "/memories/search",
            headers={"X-API-Token": "secret-token"},
            json={"scope": {"project_id": "arcade"}, "text": "x" * 200},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BYTEBOX_REQUEST_TOO_LARGE"
    assert store.calls == []


def test_trusted_host_rejection_returns_stable_error_envelope() -> None:
    store = FakeStore()
    app = create_app(store=store, api={"trusted_hosts": ["api.local"]})

    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": "evil.local"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BYTEBOX_TRUSTED_HOST_REJECTED"


def test_build_api_tls_context_configures_optional_mtls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class FakeContext:
        def __init__(self) -> None:
            self.verify_mode = ssl.CERT_NONE

        def load_cert_chain(
            self,
            *,
            certfile: str,
            keyfile: str | None = None,
            password: str | None = None,
        ) -> None:
            calls["cert_chain"] = (certfile, keyfile, password)

        def load_verify_locations(
            self,
            *,
            cafile: str | None = None,
            capath: str | None = None,
            cadata: str | None = None,
        ) -> None:
            calls["verify_locations"] = (cafile, capath, cadata)

    monkeypatch.setattr(
        "bytebox.api.security.ssl.create_default_context",
        lambda purpose: FakeContext(),
    )

    settings = ByteBoxSettings(
        api={
            "tls": {
                "enabled": True,
                "cert_file": "tls/server.crt",
                "key_file": "tls/server.key",
                "key_password": "top-secret",
                "client_ca_file": "tls/ca.crt",
                "require_client_certificate": True,
            }
        }
    )

    context = build_api_tls_context(settings.api.tls)

    assert isinstance(context, FakeContext)
    assert calls["cert_chain"] == (
        str(Path("tls/server.crt")),
        str(Path("tls/server.key")),
        "top-secret",
    )
    assert calls["verify_locations"] == (str(Path("tls/ca.crt")), None, None)
    assert context.verify_mode == ssl.CERT_REQUIRED


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
    settings = type(
        "Settings",
        (),
        {"database": type("DB", (), {"path": tmp_path / "arcade"})()},
    )()

    monkeypatch.setattr(
        "memory_store.cli.load_settings",
        lambda config_path=None: settings,
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


def test_cli_backup_and_restore_delegate_to_arcade_helpers(monkeypatch, capsys, tmp_path) -> None:
    database_path = tmp_path / "arcade"
    backup_path = tmp_path / "arcade-backup"
    settings = type(
        "Settings",
        (),
        {"database": type("DB", (), {"path": database_path})()},
    )()

    monkeypatch.setattr(
        "memory_store.cli.load_settings",
        lambda config_path=None: settings,
    )
    monkeypatch.setattr(
        "memory_store.cli.backup_arcade_database",
        lambda path, destination=None, overwrite=False: {
            "database_path": str(path),
            "backup_path": str(destination),
            "created": True,
            "overwritten": overwrite,
        },
    )
    monkeypatch.setattr(
        "memory_store.cli.restore_arcade_database",
        lambda path, backup_path=None, overwrite=False: {
            "database_path": str(path),
            "backup_path": str(backup_path),
            "restored": True,
            "overwritten": overwrite,
        },
    )

    assert main(["backup", "--out", str(backup_path), "--overwrite"]) == 0
    backup_output = json.loads(capsys.readouterr().out)
    assert backup_output["database_path"] == str(database_path)
    assert backup_output["backup_path"] == str(backup_path)
    assert backup_output["overwritten"] is True

    assert main(["restore", str(backup_path), "--overwrite"]) == 0
    restore_output = json.loads(capsys.readouterr().out)
    assert restore_output["database_path"] == str(database_path)
    assert restore_output["backup_path"] == str(backup_path)
    assert restore_output["restored"] is True


def test_cli_models_commands_manage_local_manifests(monkeypatch, capsys, tmp_path) -> None:
    source_path = tmp_path / "source-model"
    source_path.mkdir()
    (source_path / "model.onnx").write_bytes(b"fake-model")

    settings = ByteBoxSettings.model_validate(
        {
            "database": {"path": tmp_path / "data"},
            "embeddings": {
                "provider": "fastembed",
                "model": "stub-model",
                "model_path": tmp_path / "installed-model",
                "local_files_only": True,
                "hf_hub_offline": True,
                "require_manifest": True,
                "require_checksums": True,
                "model_revision": "stub/revision",
                "model_digest": "sha256:manifest",
                "dim": 4,
            },
            "reranker": {"enabled": False},
        }
    )

    monkeypatch.setattr("memory_store.cli.load_settings", lambda config_path=None: settings)

    assert main(["models", "install", "--source", str(source_path)]) == 0
    install_output = json.loads(capsys.readouterr().out)
    manifest_path = Path(install_output["manifest_path"])
    assert manifest_path.exists()
    assert install_output["verification"]["ok"] is True

    assert main(["models", "list"]) == 0
    list_output = json.loads(capsys.readouterr().out)
    assert list_output == [
        {
            "capability": "embedding",
            "provider": "fastembed",
            "model_name": "stub-model",
            "model_revision": "stub/revision",
            "model_digest": "sha256:manifest",
            "model_path": str(tmp_path / "installed-model"),
            "manifest_path": str(manifest_path),
            "strict_offline": True,
            "runtime_available": fastembed_runtime_available(),
            "require_manifest": True,
            "require_checksums": True,
        }
    ]

    assert main(["models", "inspect"]) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["identity"]["model_name"] == "stub-model"
    assert inspect_output["verification"]["ok"] is True

    assert main(["models", "verify"]) == 0
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["ok"] is True
    assert verify_output["verified_files"] == ["model.onnx"]

    export_path = tmp_path / "exported-manifest.yaml"
    assert main(["models", "export-manifest", "--out", str(export_path)]) == 0
    export_output = json.loads(capsys.readouterr().out)
    assert export_output["manifest_path"] == str(export_path)
    assert export_path.exists()

    assert main(["models", "doctor"]) == 0
    doctor_output = json.loads(capsys.readouterr().out)
    assert doctor_output["strict_offline"] is True
    assert doctor_output["verification"]["ok"] is True


def test_cli_config_migrate_rewrites_legacy_yaml_and_redacts_secrets(tmp_path, capsys) -> None:
    source_path = tmp_path / "legacy-memory-store.yaml"
    source_path.write_text(
        """
database:
  path: ./data/memory_store
api:
  local_api_token: super-secret-token
  tls:
    key_password: tls-secret
application:
  state_dir: ./state/memory_store
""".strip(),
        encoding="utf-8",
    )
    migrated_path = tmp_path / "bytebox.yaml"

    assert main(["config", "migrate", str(source_path), "--out", str(migrated_path)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["source_format"] == "yaml"
    assert output["preview"]["database"]["path"] == "./data/bytebox"
    assert output["preview"]["application"]["state_dir"] == "./state/bytebox"
    assert "local_api_token" not in output["preview"].get("api", {})
    assert "super-secret-token" not in json.dumps(output)

    migrated_text = migrated_path.read_text(encoding="utf-8")
    assert "super-secret-token" not in migrated_text
    assert "tls-secret" not in migrated_text
    assert "./data/bytebox" in migrated_text


def test_cli_config_migrate_translates_env_files_without_copying_secrets(tmp_path, capsys) -> None:
    source_path = tmp_path / "legacy.env"
    source_path.write_text(
        "\n".join(
            [
                "MEMORY_STORE_DATABASE__PATH=./data/memory_store",
                "MEMORY_STORE_APPLICATION__STATE_DIR=./state/memory_store",
                "MEMORY_STORE_API__LOCAL_API_TOKEN=super-secret-token",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    migrated_path = tmp_path / "bytebox.env"

    assert main(["config", "migrate", str(source_path), "--out", str(migrated_path)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["source_format"] == "env"
    assert output["preview"]["BYTEBOX_DATABASE__PATH"] == "./data/bytebox"
    assert output["preview"]["BYTEBOX_APPLICATION__STATE_DIR"] == "./state/bytebox"
    assert "super-secret-token" not in json.dumps(output)

    migrated_text = migrated_path.read_text(encoding="utf-8")
    assert "BYTEBOX_DATABASE__PATH=./data/bytebox" in migrated_text
    assert "BYTEBOX_API__LOCAL_API_TOKEN" not in migrated_text
    assert "super-secret-token" not in migrated_text


def test_cli_database_phase10_commands_delegate_to_operational_helpers(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    settings = ByteBoxSettings.model_validate({"database": {"path": tmp_path / "bytebox-db"}})
    calls: dict[str, Any] = {}

    monkeypatch.setattr("memory_store.cli.load_settings", lambda config_path=None: settings)
    monkeypatch.setattr(
        "memory_store.cli.inspect_database",
        lambda database_path, *, settings: {
            "command": "inspect",
            "database_path": str(database_path),
            "schema_version": settings.database.schema_version,
        },
    )

    def fake_migrate(
        source_database_path,
        *,
        settings,
        target_database_path=None,
        overwrite=False,
        dry_run=False,
        backup_destination=None,
        search_queries=(),
        verification_scope=None,
    ):
        calls["migrate"] = {
            "source_database_path": source_database_path,
            "target_database_path": target_database_path,
            "overwrite": overwrite,
            "dry_run": dry_run,
            "backup_destination": backup_destination,
            "search_queries": list(search_queries),
            "verification_scope": verification_scope.model_dump() if verification_scope else None,
            "schema_version": settings.database.schema_version,
        }
        return {"command": "migrate", **calls["migrate"]}

    def fake_verify(database_path, *, settings, search_queries=(), scope=None):
        calls["verify"] = {
            "database_path": database_path,
            "search_queries": list(search_queries),
            "scope": scope.model_dump() if scope else None,
            "schema_version": settings.database.schema_version,
        }
        return {"command": "verify", **calls["verify"]}

    def fake_reembed(database_path, *, settings, scope=None, limit=None, dry_run=False):
        calls["reembed"] = {
            "database_path": database_path,
            "scope": scope.model_dump() if scope else None,
            "limit": limit,
            "dry_run": dry_run,
            "schema_version": settings.database.schema_version,
        }
        return {"command": "reembed", **calls["reembed"]}

    monkeypatch.setattr("memory_store.cli.migrate_database", fake_migrate)
    monkeypatch.setattr("memory_store.cli.verify_database", fake_verify)
    monkeypatch.setattr("memory_store.cli.reembed_database", fake_reembed)
    monkeypatch.setattr(
        "memory_store.cli.backup_arcade_database",
        lambda path, destination=None, overwrite=False: {
            "command": "backup",
            "database_path": str(path),
            "backup_path": str(destination),
            "overwrite": overwrite,
        },
    )
    monkeypatch.setattr(
        "memory_store.cli.restore_arcade_database",
        lambda path, backup_path=None, overwrite=False: {
            "command": "restore",
            "database_path": str(path),
            "backup_path": str(backup_path),
            "overwrite": overwrite,
        },
    )

    assert main(["database", "inspect"]) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["command"] == "inspect"
    assert inspect_output["database_path"] == str(settings.database.path)

    target_path = tmp_path / "bytebox-db-migrated"
    backup_path = tmp_path / "backup-copy"
    assert (
        main(
            [
                "database",
                "migrate",
                "--dry-run",
                "--target-database-path",
                str(target_path),
                "--backup-path",
                str(backup_path),
                "--search-query",
                "thin adapters",
            ]
        )
        == 0
    )
    migrate_output = json.loads(capsys.readouterr().out)
    assert migrate_output["command"] == "migrate"
    assert migrate_output["dry_run"] is True
    assert migrate_output["target_database_path"] == str(target_path)
    assert migrate_output["search_queries"] == ["thin adapters"]
    assert migrate_output["verification_scope"] is None

    assert (
        main(["database", "verify", "--search-query", "deployment", "--project-id", "arcade"])
        == 0
    )
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["command"] == "verify"
    assert verify_output["search_queries"] == ["deployment"]
    assert verify_output["scope"] == {"user_id": None, "project_id": "arcade", "agent_id": None}

    assert main(["database", "reembed", "--dry-run", "--limit", "5"]) == 0
    reembed_output = json.loads(capsys.readouterr().out)
    assert reembed_output["command"] == "reembed"
    assert reembed_output["dry_run"] is True
    assert reembed_output["limit"] == 5
    assert reembed_output["scope"] is None

    backup_target = tmp_path / "backup"
    assert main(["database", "backup", "--out", str(backup_target), "--overwrite"]) == 0
    backup_output = json.loads(capsys.readouterr().out)
    assert backup_output["command"] == "backup"
    assert backup_output["backup_path"] == str(backup_target)

    assert main(["database", "restore", str(backup_target), "--overwrite"]) == 0
    restore_output = json.loads(capsys.readouterr().out)
    assert restore_output["command"] == "restore"
    assert restore_output["backup_path"] == str(backup_target)
