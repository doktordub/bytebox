from __future__ import annotations

from datetime import datetime, timezone

import pytest

import memory_store.service as service_module
from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.embeddings.fastembed_provider import EmbeddedText
from memory_store.errors import EmbeddingDimensionMismatchError
from memory_store.models import MemoryCreate, Scope

if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 4 service tests", allow_module_level=True)


class _FakeProvider:
    seen_texts: list[str] = []

    def __init__(
        self,
        model: str = "stub-model",
        model_version: str | None = None,
        batch_size: int = 64,
        normalize: bool = True,
        reranker_model: str | None = None,
    ) -> None:
        self.model = model
        self.model_version = model_version or "stub/revision"
        self.batch_size = batch_size
        self.normalize = normalize
        self.reranker_model = reranker_model

    def embed_text(self, text: str) -> EmbeddedText:
        type(self).seen_texts.append(text)
        return EmbeddedText(
            vector=[0.5, 0.5, 0.5, 0.5],
            model=self.model,
            model_version=self.model_version,
            dim=4,
            created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddedText]:
        return [self.embed_text(text) for text in texts]


def test_memory_store_add_memory_can_embed_before_insert(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model", "model_version": "stub/revision"},
    )

    try:
        created = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                title="Thin adapters",
                summary="Routes through the service layer",
                text="MemoryStore calls MemoryService.",
                tags=["api", "service"],
                source_path="docs/architecture.md",
                heading_path=["Layered Architecture", "Adapter Layer"],
                metadata={"frontmatter": {"owner": "copilot", "priority": "high"}},
            ),
            embed=True,
        )

        loaded = store.get_memory(created.memory_id)
        assert loaded is not None
        assert created.embedding == [0.5, 0.5, 0.5, 0.5]
        assert created.embedding_model == "stub-model"
        assert created.embedding_model_version == "stub/revision"
        assert created.embedding_dim == 4
        assert created.embedding_created_at == datetime(2026, 6, 13, tzinfo=timezone.utc)
        assert loaded.embedding_model == "stub-model"

        assert len(_FakeProvider.seen_texts) == 1
        embedding_text = _FakeProvider.seen_texts[0]
        assert "Title: Thin adapters" in embedding_text
        assert "Summary: Routes through the service layer" in embedding_text
        assert "Tags: api, service" in embedding_text
        assert "Source: type=manual; path=docs/architecture.md" in embedding_text
        assert "Headings: Layered Architecture > Adapter Layer" in embedding_text
        assert "Frontmatter: owner=copilot; priority=high" in embedding_text
        assert embedding_text.endswith("MemoryStore calls MemoryService.")
    finally:
        store.close()


def test_memory_store_rejects_mismatched_embedding_dimensions_by_default(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        with pytest.raises(EmbeddingDimensionMismatchError):
            store.add_memory(
                MemoryCreate(text="bad embedding", embedding=[1.0, 2.0, 3.0], embedding_dim=3)
            )
    finally:
        store.close()


def test_memory_store_quarantines_mismatched_embedding_dimensions(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "dimension_mismatch": "quarantine"},
    )

    try:
        created = store.add_memory(
            MemoryCreate(
                text="legacy embedding",
                embedding=[1.0, 2.0, 3.0],
                embedding_dim=3,
                embedding_model="legacy-model",
                embedding_model_version="legacy-rev",
            )
        )

        assert created.embedding is None
        assert created.embedding_dim is None
        assert created.embedding_created_at is None
        assert created.metadata["embedding_quarantined"] is True
        assert created.metadata["embedding_quarantine_original_dim"] == 3
        assert created.metadata["embedding_quarantine_original_model"] == "legacy-model"
        assert created.metadata["embedding_quarantine_original_model_version"] == "legacy-rev"
    finally:
        store.close()


def test_memory_store_reembeds_mismatched_embeddings_when_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "dimension_mismatch": "reembed", "model": "stub-model"},
    )

    try:
        created = store.add_memory(
            MemoryCreate(text="refresh this embedding", embedding=[1.0, 2.0, 3.0], embedding_dim=3)
        )

        assert created.embedding == [0.5, 0.5, 0.5, 0.5]
        assert created.embedding_model == "stub-model"
        assert created.embedding_dim == 4
        assert created.embedding_created_at == datetime(2026, 6, 13, tzinfo=timezone.utc)
    finally:
        store.close()


def test_memory_store_validates_existing_index_dimensions(tmp_path) -> None:
    first = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )
    first.health()
    first.close()

    with pytest.raises(EmbeddingDimensionMismatchError):
        second = MemoryStore.from_config(
            database={"path": tmp_path / "arcade", "schema_version": 1},
            embeddings={"dim": 8},
        )
        second.health()


def test_memory_store_reembeds_incompatible_embedding_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={
            "dim": 4,
            "dimension_mismatch": "reembed",
            "model": "stub-model",
            "model_revision": "stub/revision",
        },
    )

    try:
        created = store.add_memory(
            MemoryCreate(
                text="refresh the legacy provider identity",
                embedding=[0.1, 0.2, 0.3, 0.4],
                embedding_dim=4,
                embedding_model="legacy-model",
                embedding_model_version="legacy-revision",
            )
        )

        assert created.embedding == [0.5, 0.5, 0.5, 0.5]
        assert created.embedding_model == "stub-model"
        assert created.embedding_model_version == "stub/revision"
        assert created.embedding_dim == 4
    finally:
        store.close()