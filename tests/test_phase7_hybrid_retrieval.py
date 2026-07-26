from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

import memory_store.retrieval.rerank as rerank_module
import memory_store.service as service_module
from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.embeddings.fastembed_provider import EmbeddedText
from memory_store.models import MemoryCreate, MemorySearchQuery, MemoryStatus, MemoryType, Scope

if not arcade_runtime_available():
    pytest.skip(
        "arcadedb_embedded is required for phase 7 retrieval tests",
        allow_module_level=True,
    )


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
        lowered = text.lower()
        if "alpha" in lowered or "deployment" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        elif "beta" in lowered:
            vector = [0.0, 1.0, 0.0, 0.0]
        else:
            vector = [0.0, 0.0, 1.0, 0.0]
        return EmbeddedText(
            vector=vector,
            model=self.model,
            model_version=self.model_version,
            dim=4,
            created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddedText]:
        return [self.embed_text(text) for text in texts]


class _FakeReranker:
    seen_queries: list[str] = []
    seen_documents: list[list[str]] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def rerank(self, query: str, documents: list[str], batch_size: int = 32, **_: object):
        type(self).seen_queries.append(query)
        type(self).seen_documents.append(list(documents))
        scores_by_document = {
            "alpha rerank second": 0.95,
            "alpha rerank first": 0.15,
        }
        for document in documents:
            yield scores_by_document.get(document, 0.05)


def _sorted_document_chunks(store: MemoryStore, source_path: str, scope: Scope):
    repository = store._service._repository()
    return sorted(
        repository.list_by_source_path(
            source_path,
            scope=scope,
            memory_type=MemoryType.DOCUMENT_CHUNK,
        ),
        key=lambda record: (
            int(record.metadata["section_index"]),
            int(record.chunk_index or 0),
            record.memory_id,
        ),
    )


def test_search_combines_vector_and_full_text_with_rrf_and_filters(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
    )

    try:
        vector_only = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Thin adapters keep the service layer isolated.",
                embedding=[0.6, 0.8, 0.0, 0.0],
            )
        )
        hybrid = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Alpha design alpha design reference for the deployment architecture.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        full_text_only = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Alpha design guidance for rollout checklists.",
            )
        )
        store.add_memory(
            MemoryCreate(
                scope=scope,
                status=MemoryStatus.CANDIDATE,
                text="Alpha design candidate that should stay out of active retrieval.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        store.add_memory(
            MemoryCreate(
                scope=scope,
                allow_retrieval=False,
                text="Alpha design hidden by retrieval policy.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )

        results = store.search(MemorySearchQuery(scope=scope, text="alpha design", limit=5))

        assert [result.memory.memory_id for result in results] == [
            hybrid.memory_id,
            full_text_only.memory_id,
            vector_only.memory_id,
        ]

        top = results[0]
        assert top.memory.memory_id == hybrid.memory_id
        assert top.component_scores["retrieval_fusion"] == pytest.approx(2.0 / 61.0)
        assert top.component_scores["vector"] == pytest.approx(1.0)
        assert top.component_scores["full_text"] > 0.0
        assert top.debug["sources"] == ["vector", "full_text"]
        assert top.debug["source_ranks"] == {"vector": 1, "full_text": 1}
        assert 0.0 <= top.final_score <= 1.0

        assert all(result.memory.status == MemoryStatus.ACTIVE for result in results)
        assert all(result.memory.allow_retrieval is True for result in results)
    finally:
        store.close()


def test_generic_search_can_mix_document_chunks_with_other_memory_types(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Deployment

Alpha deployment architecture reference.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
    )

    try:
        store.ingest_document(path, scope)
        manual = store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.DECISION,
                text="Alpha deployment architecture note for a manual decision.",
            )
        )

        results = store.search(
            MemorySearchQuery(
                scope=scope,
                text="alpha deployment architecture",
                limit=5,
            )
        )

        result_types = {result.memory.memory_type for result in results}

        assert MemoryType.DOCUMENT_CHUNK in result_types
        assert MemoryType.DECISION in result_types
        assert any(result.memory.memory_id == manual.memory_id for result in results)
    finally:
        store.close()

def test_chunk_search_contract_returns_chunk_first_results(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Overview

Platform overview notes.

## Deployment

Deployment architecture alpha guidance.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
    )

    try:
        store.ingest_document(path, scope)
        manual = store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.DECISION,
                text="Deployment architecture alpha decision note.",
            )
        )

        source_path = path.resolve().as_posix()
        chunks = _sorted_document_chunks(store, source_path, scope)
        deployment_chunk = next(
            record
            for record in chunks
            if tuple(record.heading_path or ()) == ("Architecture", "Deployment")
        )
        deployment_index = next(
            index for index, record in enumerate(chunks) if record.memory_id == deployment_chunk.memory_id
        )

        chunk_store = cast(Any, store)
        results = chunk_store.search_document_chunks(
            text="deployment architecture alpha",
            scope=scope,
            limit=5,
        )

        assert results[0].chunk_id == deployment_chunk.chunk_id
        assert all(result.memory_id != manual.memory_id for result in results)

        top = results[0]
        assert top.memory_id == deployment_chunk.memory_id
        assert top.source_path == source_path
        assert top.source_hash == deployment_chunk.source_hash
        assert top.title == deployment_chunk.title
        assert top.summary == deployment_chunk.summary
        assert top.text == deployment_chunk.text
        assert top.tags == deployment_chunk.tags
        assert top.heading_path == deployment_chunk.heading_path
        assert top.section_index == deployment_chunk.metadata["section_index"]
        assert top.section_chunk_index == deployment_chunk.chunk_index
        assert top.document_chunk_index == deployment_index
        assert 0.0 <= top.final_score <= 1.0
        assert "retrieval_fusion" in top.component_scores
    finally:
        store.close()

def test_chunk_context_can_cross_section_boundaries(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Alpha

Alpha introduction.

## Beta

Beta deployment guidance.

## Gamma

Gamma rollout checks.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
    )

    try:
        store.ingest_document(path, scope)

        chunks = _sorted_document_chunks(store, path.resolve().as_posix(), scope)
        beta_index = next(
            index
            for index, record in enumerate(chunks)
            if tuple(record.heading_path or ()) == ("Architecture", "Beta")
        )
        beta_chunk = chunks[beta_index]

        chunk_store = cast(Any, store)
        context = chunk_store.get_chunk_context(
            beta_chunk.chunk_id,
            scope=scope,
            before=1,
            after=1,
        )

        assert context.chunk.chunk_id == beta_chunk.chunk_id
        assert context.chunk.document_chunk_index == beta_index
        assert [item.chunk_id for item in context.before] == [chunks[beta_index - 1].chunk_id]
        assert [item.chunk_id for item in context.after] == [chunks[beta_index + 1].chunk_id]
    finally:
        store.close()


def test_search_deduplicates_across_stable_key_chunk_id_and_source_hash(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
    )

    try:
        primary = store.add_memory(
            MemoryCreate(
                scope=scope,
                stable_key="doc:alpha",
                chunk_id="chunk-alpha",
                source_hash="hash-alpha",
                text="Alpha design alpha design primary document chunk.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        duplicate_stable = store.add_memory(
            MemoryCreate(
                scope=scope,
                stable_key="doc:alpha",
                chunk_id="chunk-beta",
                source_hash="hash-beta",
                text="Alpha design duplicate that shares the stable key.",
            )
        )
        duplicate_chunk = store.add_memory(
            MemoryCreate(
                scope=scope,
                stable_key="doc:gamma",
                chunk_id="chunk-alpha",
                source_hash="hash-gamma",
                text="Alpha design duplicate that shares the chunk id.",
            )
        )
        duplicate_hash = store.add_memory(
            MemoryCreate(
                scope=scope,
                stable_key="doc:delta",
                chunk_id="chunk-delta",
                source_hash="hash-alpha",
                text="Alpha design duplicate that shares the source hash.",
            )
        )

        results = store.search(MemorySearchQuery(scope=scope, text="alpha design", limit=10))

        assert [result.memory.memory_id for result in results] == [primary.memory_id]
        assert sorted(results[0].debug["duplicate_memory_ids"]) == sorted(
            [
                duplicate_stable.memory_id,
                duplicate_chunk.memory_id,
                duplicate_hash.memory_id,
            ]
        )
    finally:
        store.close()


def test_search_graph_expansion_is_one_hop_and_edge_labeled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": True, "graph_expansion_hops": 1},
    )

    try:
        anchor = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Deployment architecture anchor for graph expansion.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        one_hop = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Neighbor only reachable through a RELATED_TO edge.",
            )
        )
        two_hops = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Second hop that must not be returned by phase 7 expansion.",
            )
        )

        repository = store._service._repository()
        repository.create_edge(anchor.memory_id, one_hop.memory_id, "RELATED_TO")
        repository.create_edge(one_hop.memory_id, two_hops.memory_id, "RELATED_TO")

        results = store.search(
            MemorySearchQuery(scope=scope, text="deployment architecture", limit=10)
        )
        by_id = {result.memory.memory_id: result for result in results}

        assert anchor.memory_id in by_id
        assert one_hop.memory_id in by_id
        assert two_hops.memory_id not in by_id

        expanded = by_id[one_hop.memory_id]
        assert expanded.component_scores["graph"] > 0.0
        assert expanded.debug["graph_expanded"] is True
        assert expanded.debug["graph_sources"] == [
            {"memory_id": anchor.memory_id, "edge_type": "RELATED_TO"}
        ]
    finally:
        store.close()


def test_search_reranking_is_bounded(monkeypatch, tmp_path) -> None:
    _FakeReranker.seen_queries.clear()
    _FakeReranker.seen_documents.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)
    monkeypatch.setattr(rerank_module, "TextCrossEncoder", _FakeReranker)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": True, "model": "stub-reranker", "top_n": 2},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        first = store.add_memory(
            MemoryCreate(scope=scope, text="alpha rerank first", embedding=[1.0, 0.0, 0.0, 0.0])
        )
        second = store.add_memory(
            MemoryCreate(scope=scope, text="alpha rerank second", embedding=[0.99, 0.01, 0.0, 0.0])
        )
        third = store.add_memory(
            MemoryCreate(scope=scope, text="alpha rerank third", embedding=[0.98, 0.02, 0.0, 0.0])
        )

        results = store.search(MemorySearchQuery(scope=scope, text="alpha", limit=3))

        assert _FakeReranker.seen_queries == ["alpha"]
        assert len(_FakeReranker.seen_documents) == 1
        assert sorted(_FakeReranker.seen_documents[0]) == sorted(
            ["alpha rerank first", "alpha rerank second"]
        )
        assert [result.memory.memory_id for result in results] == [
            second.memory_id,
            first.memory_id,
            third.memory_id,
        ]
        assert results[0].debug["rerank_applied"] is True
        assert results[1].debug["rerank_applied"] is True
        assert results[2].debug["rerank_applied"] is False
    finally:
        store.close()