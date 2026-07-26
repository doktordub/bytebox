from __future__ import annotations

import importlib

import pytest

from memory_store import MemoryCreate, MemoryRecord, MemorySearchQuery, MemoryStore, Scope

MODULES = [
    "memory_store",
    "memory_store.config",
    "memory_store.errors",
    "memory_store.models",
    "memory_store.store",
    "memory_store.service",
    "memory_store.scoring",
    "memory_store.lifecycle",
    "memory_store.privacy",
    "memory_store.cli",
    "memory_store.ingestion.markdown",
    "memory_store.retrieval.vector",
    "memory_store.arcade.connection",
    "memory_store.embeddings.fastembed_provider",
    "memory_store.api.main",
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
    assert MemoryStore is not None
    assert MemoryCreate is not None
    assert MemoryRecord is not None
    assert MemorySearchQuery is not None
    assert Scope is not None
