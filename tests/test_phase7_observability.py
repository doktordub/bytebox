from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bytebox.api.main import create_inprocess_app as create_app
from bytebox.embeddings import get_current_traceparent
from bytebox.models import HealthStatus, InventoryDetailLevel, MemoryInventoryReport, MemoryStats


class _ReadyStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.traceparents: list[str | None] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def health(self) -> HealthStatus:
        self.traceparents.append(get_current_traceparent())
        return HealthStatus(
            status="ok",
            database_path=self.database_path,
            schema_version=1,
            dependencies={"arcadedb_embedded": True, "fastembed": True},
            message="token=secret-123\nsecond line",
        )

    def stats(self) -> MemoryStats:
        return MemoryStats(
            total_records=3,
            scope_counts={"global": 1, "scoped": 2},
            status_counts={"active": 3},
            type_counts={"decision": 1, "observation": 2},
        )

    def inventory(
        self,
        *,
        detail: InventoryDetailLevel | str = InventoryDetailLevel.SUMMARY,
        include_names: bool = False,
        names_limit: int = 100,
        include_document_chunks: bool = True,
    ) -> MemoryInventoryReport:
        del include_document_chunks
        detail_level = InventoryDetailLevel(detail)
        response = {
            "detail": detail_level,
            "generated_at": "2025-01-01T00:00:00Z",
            "summary": {
                "total_records": 3,
                "scope_counts": {"global": 1, "scoped": 2},
                "status_counts": {"active": 2, "forgotten": 1},
                "type_counts": {"decision": 1, "observation": 2},
            },
        }
        if detail_level == InventoryDetailLevel.FULL:
            response["scopes"] = {
                "distinct_scope_tuples": 2,
                "global_records": 1,
                "scoped_records": 2,
                "user_ids": {
                    "count": 1,
                    "names": ["alice"][:names_limit] if include_names else [],
                    "truncated": False,
                    "remaining": 0,
                },
                "project_ids": {
                    "count": 1,
                    "names": ["secret-project"][:names_limit] if include_names else [],
                    "truncated": False,
                    "remaining": 0,
                },
                "agent_ids": {
                    "count": 1,
                    "names": ["copilot-secret"][:names_limit] if include_names else [],
                    "truncated": False,
                    "remaining": 0,
                },
            }
            response["memory_types"] = [
                {
                    "memory_type": "decision",
                    "display_name": "Decision",
                    "count": 1,
                    "status_counts": {"active": 1},
                    "scope_counts": {"global": 0, "scoped": 1},
                    "oldest_created_at": "2025-01-01T00:00:00Z",
                    "newest_updated_at": "2025-01-02T00:00:00Z",
                },
                {
                    "memory_type": "observation",
                    "display_name": "Observation",
                    "count": 2,
                    "status_counts": {"active": 1, "forgotten": 1},
                    "scope_counts": {"global": 1, "scoped": 1},
                    "oldest_created_at": "2025-01-03T00:00:00Z",
                    "newest_updated_at": "2025-01-04T00:00:00Z",
                },
            ]
        return MemoryInventoryReport.model_validate(response)


class _NotReadyStore(_ReadyStore):
    def health(self) -> HealthStatus:
        return HealthStatus(
            status="degraded",
            database_path=self.database_path,
            schema_version=1,
            dependencies={"arcadedb_embedded": False, "fastembed": False},
            message="provider secret-token unavailable",
        )


def test_trace_context_propagates_and_ready_health_uses_safe_contract(tmp_path: Path) -> None:
    store = _ReadyStore(tmp_path / "secret" / "arcade")
    app = create_app(
        store=store,
        api={"local_api_token": "secret-token"},
        database={"path": store.database_path},
        logging={"level": "off"},
    )

    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "alive"
        assert live.headers["X-Trace-ID"]

        ready = client.get(
            "/health/ready",
            headers={
                "X-API-Token": "secret-token",
                "traceparent": traceparent,
            },
        )
        assert ready.status_code == 200
        payload = ready.json()
        assert payload["status"] == "ready"
        assert ready.headers["X-Trace-ID"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert payload["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert store.traceparents == [traceparent]


def test_status_and_state_redact_paths_and_secrets(tmp_path: Path) -> None:
    database_path = tmp_path / "very-secret-folder" / "arcade"
    store = _ReadyStore(database_path)
    app = create_app(
        store=store,
        api={
            "local_api_token": "secret-token",
            "local_api_token_scopes": ["admin:read"],
        },
        database={"path": database_path},
        logging={"level": "off"},
    )

    with TestClient(app) as client:
        headers = {"X-API-Token": "secret-token"}

        status_response = client.get("/status", headers=headers)
        assert status_response.status_code == 200
        assert status_response.json()["logging"]["level"] == "off"
        assert str(database_path) not in status_response.text
        assert "secret-123" not in status_response.text

        state_response = client.get(
            "/state",
            headers={**headers, "X-Confirm-Delete": "hard-delete"},
        )
        assert state_response.status_code == 403


def test_readiness_returns_503_when_required_dependencies_are_unavailable(tmp_path: Path) -> None:
    store = _NotReadyStore(tmp_path / "arcade")
    app = create_app(
        store=store,
        api={"local_api_token": "secret-token"},
        database={"path": store.database_path},
        logging={"level": "off"},
    )

    with TestClient(app) as client:
        response = client.get("/health/ready", headers={"X-API-Token": "secret-token"})
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert {item["name"]: item["status"] for item in response.json()["checks"]}["database"] == "not_ready"


def test_metrics_endpoint_and_off_logging_do_not_emit_bytebox_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = _ReadyStore(tmp_path / "arcade")
    app = create_app(
        store=store,
        api={
            "local_api_token": "secret-token",
            "metrics_anonymous": False,
        },
        database={"path": store.database_path},
        logging={"level": "off", "metrics_enabled": True},
    )

    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            unauthorized = client.get("/metrics")
            assert unauthorized.status_code == 401

            metrics = client.get("/metrics", headers={"X-API-Token": "secret-token"})
            assert metrics.status_code == 200
            assert "bytebox_uptime_seconds" in metrics.text
            assert "bytebox_inflight_operations" in metrics.text
            assert "bytebox_memory_records_total" in metrics.text
            assert 'bytebox_memory_scope_records_total{scope_kind="global"} 1.0' in metrics.text
            assert 'bytebox_memory_scope_records_total{scope_kind="scoped"} 2.0' in metrics.text
            assert 'bytebox_memory_scope_dimension_total{dimension="project_id"} 1.0' in metrics.text
            assert "bytebox_memory_scope_tuples_total 2.0" in metrics.text
            assert "bytebox_inventory_report_generated_seconds 1735689600.0" in metrics.text
            assert "alice" not in metrics.text
            assert "secret-project" not in metrics.text
            assert str(store.database_path) not in metrics.text

    assert not [record for record in caplog.records if record.name.startswith("bytebox")]