from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import memory_store.service as service_module
import pytest
from memory_store.arcade import arcade_runtime_available
from memory_store.models import MemoryCreate, MemorySearchQuery, MemoryStatus, MemoryType, Scope
from memory_store.retrieval.filters import build_hard_filter, normalize_query

from bytebox.services.administration import AdministrationService
from memory_store import MemoryStore


class _FakeProvider:
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

    def embed_text(self, text: str):
        from memory_store.embeddings.fastembed_provider import EmbeddedText

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
            created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    def embed_batch(self, texts: list[str]) -> list[Any]:
        return [self.embed_text(text) for text in texts]


class _StatsRepository:
    def aggregate_stats(self) -> dict[str, object]:
        raise AssertionError("stats should be projected from the inventory summary builder")

    def aggregate_inventory_summary(
        self,
        *,
        include_document_chunks: bool = True,
    ) -> dict[str, object]:
        assert include_document_chunks is True
        return {
            "total_records": 7,
            "scope_counts": {"global": 2, "scoped": 5},
            "status_counts": {"active": 6, "forgotten": 1},
            "type_counts": {"observation": 4, "document_chunk": 3},
        }

    def count_memories(self, *args: object, **kwargs: object) -> int:
        raise AssertionError("legacy count_memories loop should not be used")


class _StatsOwner:
    def __init__(self) -> None:
        self._repo = _StatsRepository()

    def _repository(self) -> _StatsRepository:
        return self._repo


@pytest.mark.skipif(
    not arcade_runtime_available(),
    reason="arcadedb_embedded is required for phase 8 runtime optimization tests",
)
def test_search_uses_repository_bounded_candidate_queries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.OBSERVATION,
                text="Alpha deployment architecture guidance.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.OBSERVATION,
                status=MemoryStatus.CANDIDATE,
                text="Alpha candidate that should stay filtered out.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        repository = store._service._repository()

        def _fail_list_by_scope(_scope: Scope) -> list[Any]:
            raise AssertionError("search should not materialize the full scope")

        monkeypatch.setattr(repository, "list_by_scope", _fail_list_by_scope)

        results = store.search(
            MemorySearchQuery(scope=scope, text="alpha deployment architecture", limit=5)
        )

        assert results
        assert all(result.memory.status == MemoryStatus.ACTIVE for result in results)
    finally:
        store.close()


@pytest.mark.skipif(
    not arcade_runtime_available(),
    reason="arcadedb_embedded is required for phase 8 runtime optimization tests",
)
def test_full_text_candidate_plan_uses_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.OBSERVATION,
                text="Alpha deployment architecture guidance.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        repository = store._service._repository()
        query = MemorySearchQuery(scope=scope, text="alpha deployment architecture", limit=5)

        plan = repository.explain_full_text_candidate_query(
            hard_filter=build_hard_filter(query),
            query=normalize_query(query),
            top_n=5,
            oversample=2,
        )

        assert "FETCH FROM INDEX MemoryRecord[text]" in plan
        assert "CONTAINSTEXT" in plan
    finally:
        store.close()


def test_administration_stats_use_grouped_aggregate_snapshot() -> None:
    stats = AdministrationService(_StatsOwner()).stats()

    assert stats.total_records == 7
    assert stats.scope_counts == {"global": 2, "scoped": 5}
    assert stats.status_counts == {"active": 6, "forgotten": 1}
    assert stats.type_counts == {"observation": 4, "document_chunk": 3}