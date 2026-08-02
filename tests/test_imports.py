from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from bytebox import (
    ByteBox,
    ByteBoxSettings,
    MemoryCreate,
    MemoryRecord,
    MemorySearchQuery,
    MemoryStore,
    Scope,
)

MODULES = [
    "bytebox",
    "bytebox.config",
    "bytebox.errors",
    "bytebox.models",
    "bytebox.store",
    "bytebox.service",
    "bytebox.scoring",
    "bytebox.lifecycle",
    "bytebox.privacy",
    "bytebox.cli",
    "bytebox.ingestion.markdown",
    "bytebox.retrieval.vector",
    "bytebox.arcade.connection",
    "bytebox.bootstrap.container",
    "bytebox.bootstrap.lifespan",
    "bytebox.embeddings.fastembed_provider",
    "bytebox.embeddings.ollama_provider",
    "bytebox.embeddings.llamacpp_provider",
    "bytebox.embeddings.remote_http",
    "bytebox.api.main",
    "memory_store",
    "memory_store.config",
    "memory_store.store",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_health_smoke(tmp_path) -> None:
    store = MemoryStore.from_config(database={"path": tmp_path / "arcade"}, embeddings={"dim": 4})

    try:
        health = store.health()

        assert health.status == "ok"
        assert health.database_path == tmp_path / "arcade"
        assert health.dependencies["arcadedb_embedded"] is True
    finally:
        store.close()


def test_package_exports_common_api_types() -> None:
    assert ByteBox is not None
    assert ByteBoxSettings is not None
    assert MemoryStore is not None
    assert MemoryCreate is not None
    assert MemoryRecord is not None
    assert MemorySearchQuery is not None
    assert Scope is not None


def test_bytebox_cli_prog_name() -> None:
    from bytebox.cli import build_parser

    assert build_parser().prog == "bytebox"


def test_bytebox_api_title() -> None:
    from bytebox.api.main import create_inprocess_app

    class NoopStore:
        def close(self) -> None:
            return None

    app = create_inprocess_app(store=NoopStore())

    assert app.title == "ByteBox"
    assert app.docs_url is None
    assert app.openapi_url is None


def test_bytebox_rest_shim_loads_swagger_routes() -> None:
    from bytebox.api.main import create_app, create_inprocess_app

    class NoopStore:
        def close(self) -> None:
            return None

    with TestClient(create_app(store=NoopStore())) as shim_client:
        assert shim_client.get("/docs").status_code == 200
        assert shim_client.get("/openapi.json").status_code == 200

    with TestClient(create_inprocess_app(store=NoopStore())) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
