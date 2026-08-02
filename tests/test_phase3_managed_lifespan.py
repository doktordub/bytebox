from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

import memory_store.service as service_module
from memory_store import MemoryStore
from memory_store.api.main import create_inprocess_app as create_app
from memory_store.arcade import arcade_runtime_available
from memory_store.arcade.connection import normalize_database_path
from memory_store.bootstrap.container import ReadinessState
from memory_store.errors import ConfigError, PersistenceError


if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 3 lifespan tests", allow_module_level=True)


def _lock_path_for(database_path: Path) -> Path:
    normalized = normalize_database_path(database_path)
    return normalized.parent / f"{normalized.name}.lock"


def test_memory_store_from_config_is_lazy_until_first_operation(tmp_path: Path) -> None:
    database_path = tmp_path / "arcade"
    store = MemoryStore.from_config(database={"path": database_path, "schema_version": 1}, embeddings={"dim": 4})

    assert database_path.exists() is False
    assert store._service is None

    try:
        health = store.health()
        assert health.status == "ok"
        assert database_path.exists() is True
        assert store._container is not None
        assert store._container.readiness_state is ReadinessState.READY
    finally:
        store.close()

    assert store._container is not None
    assert store._container.readiness_state is ReadinessState.CLOSED


def test_create_app_defers_database_open_until_lifespan(tmp_path: Path) -> None:
    database_path = tmp_path / "arcade"
    app = create_app(
        database={"path": database_path, "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    assert database_path.exists() is False
    assert app.state.bytebox_container is not None
    assert app.state.bytebox_container.readiness_state is ReadinessState.NEW

    with TestClient(app) as client:
        assert database_path.exists() is True
        assert app.state.bytebox_container.readiness_state is ReadinessState.READY
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    assert _lock_path_for(database_path).exists() is False
    assert app.state.bytebox_container.readiness_state is ReadinessState.CLOSED


def test_lifespan_startup_failure_releases_partial_resources(monkeypatch, tmp_path: Path) -> None:
    database_path = tmp_path / "arcade"

    def fail_migrations(*args, **kwargs):
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(service_module, "run_database_migrations", fail_migrations)

    app = create_app(
        database={"path": database_path, "schema_version": 1},
        embeddings={"dim": 4},
    )

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        with TestClient(app):
            pass

    assert _lock_path_for(database_path).exists() is False
    assert app.state.bytebox_container.readiness_state is ReadinessState.FAILED


def test_create_app_rejects_multiple_workers_for_embedded_mode(tmp_path: Path) -> None:
    database_path = tmp_path / "arcade"
    app = create_app(
        database={"path": database_path, "schema_version": 1, "embedded_single_process": True},
        embeddings={"dim": 4},
        api={"workers": 2},
    )

    with pytest.raises(ConfigError, match="workers == 1"):
        with TestClient(app):
            pass

    assert database_path.exists() is False
    assert app.state.bytebox_container.readiness_state is ReadinessState.FAILED


def test_memory_store_close_is_idempotent_after_initialization(tmp_path: Path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    store.health()
    store.close()
    store.close()

    assert store._container is not None
    assert store._container.readiness_state is ReadinessState.CLOSED


def test_shutdown_rejects_new_operations_while_draining_in_flight_work(tmp_path: Path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )
    store.health()
    assert store._container is not None

    operation_started = Event()
    release_operation = Event()

    def hold_operation() -> None:
        assert store._container is not None
        with store._container.operation("held"):
            operation_started.set()
            release_operation.wait(timeout=2)

    worker = Thread(target=hold_operation)
    worker.start()
    assert operation_started.wait(timeout=2) is True

    closer = Thread(target=store.close)
    closer.start()

    with pytest.raises(PersistenceError, match="not ready"):
        assert store._container is not None
        with store._container.operation("rejected"):
            pass

    release_operation.set()
    worker.join(timeout=2)
    closer.join(timeout=2)

    assert store._container.readiness_state is ReadinessState.CLOSED