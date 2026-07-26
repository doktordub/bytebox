from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp

import pytest

import memory_store.service as service_module
from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.config import MemoryStoreSettings
from memory_store.embeddings.fastembed_provider import EmbeddedText
from memory_store.models import (
    MemoryCreate,
    MemoryRecord,
    MemorySearchQuery,
    MemoryStatus,
    MemoryType,
    Scope,
)
from memory_store.retrieval.types import RetrievalCandidate
from memory_store.scoring import (
    enrich_candidate_scores,
    normalize_candidate_scores,
    score_candidates,
)

_NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


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

    def embed_text(self, text: str) -> EmbeddedText:
        lowered = text.lower()
        if "alpha" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        else:
            vector = [0.0, 1.0, 0.0, 0.0]
        return EmbeddedText(
            vector=vector,
            model=self.model,
            model_version=self.model_version,
            dim=4,
            created_at=_NOW,
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddedText]:
        return [self.embed_text(text) for text in texts]


def _record(
    memory_id: str,
    *,
    memory_type: MemoryType = MemoryType.OBSERVATION,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    confidence: float = 0.5,
    importance: float = 0.5,
    user_rating: float | None = None,
    superseded_by: str | None = None,
    source_path: str | None = None,
) -> MemoryRecord:
    created = created_at or updated_at or _NOW
    updated = updated_at or created
    return MemoryRecord(
        memory_id=memory_id,
        scope=Scope(project_id="arcade"),
        text=f"memory {memory_id}",
        memory_type=memory_type,
        status=status,
        confidence=confidence,
        importance=importance,
        user_rating=user_rating,
        created_at=created,
        updated_at=updated,
        superseded_by=superseded_by,
        source_path=source_path,
    )


def test_temporal_scoring_respects_memory_type_rules() -> None:
    settings = MemoryStoreSettings()
    candidates = [
        RetrievalCandidate(
            memory=_record(
                "observation",
                memory_type=MemoryType.OBSERVATION,
                updated_at=_NOW - timedelta(days=28),
            ),
            component_scores={"retrieval_fusion": 0.20},
        ),
        RetrievalCandidate(
            memory=_record(
                "decision",
                memory_type=MemoryType.DECISION,
                updated_at=_NOW - timedelta(days=700),
            ),
            component_scores={"retrieval_fusion": 0.20},
        ),
        RetrievalCandidate(
            memory=_record(
                "decision-superseded",
                memory_type=MemoryType.DECISION,
                status=MemoryStatus.SUPERSEDED,
                superseded_by="replacement",
                updated_at=_NOW - timedelta(days=700),
            ),
            component_scores={"retrieval_fusion": 0.20},
        ),
        RetrievalCandidate(
            memory=_record(
                "chunk",
                memory_type=MemoryType.DOCUMENT_CHUNK,
                updated_at=_NOW - timedelta(days=700),
                source_path="docs/architecture.md",
            ),
            component_scores={"retrieval_fusion": 0.20},
        ),
    ]

    enrich_candidate_scores(candidates, temporal_settings=settings.scoring.temporal, now=_NOW)

    assert candidates[0].component_scores["temporal"] == pytest.approx(exp(-2.0))
    assert candidates[0].debug["temporal_scoring"]["reason"] == "decay"
    assert candidates[1].component_scores["temporal"] == pytest.approx(1.0)
    assert candidates[1].debug["temporal_scoring"]["reason"] == "decision_no_decay"
    assert candidates[2].component_scores["temporal"] == pytest.approx(0.0)
    assert candidates[2].debug["temporal_scoring"]["reason"] == "decision_superseded"
    assert candidates[3].component_scores["temporal"] == pytest.approx(1.0)
    assert candidates[3].debug["temporal_scoring"]["reason"] == "document_chunk_no_decay"


def test_score_candidates_redistributes_disabled_reranker_weight_and_tracks_missing_inputs(
) -> None:
    settings = MemoryStoreSettings()
    candidates = [
        RetrievalCandidate(
            memory=_record(
                "primary",
                memory_type=MemoryType.OBSERVATION,
                updated_at=_NOW - timedelta(days=7),
                confidence=0.8,
                importance=0.9,
            ),
            component_scores={
                "retrieval_fusion": 0.30,
                "vector": 0.90,
                "full_text": 2.0,
            },
        ),
        RetrievalCandidate(
            memory=_record(
                "secondary",
                memory_type=MemoryType.OBSERVATION,
                updated_at=_NOW - timedelta(days=21),
                confidence=0.4,
                importance=0.3,
                user_rating=0.2,
            ),
            component_scores={
                "retrieval_fusion": 0.15,
                "vector": 0.45,
                "full_text": 1.0,
            },
        ),
    ]

    enrich_candidate_scores(candidates, temporal_settings=settings.scoring.temporal, now=_NOW)
    normalize_candidate_scores(candidates)
    score_candidates(
        candidates,
        settings.scoring.weights.model_dump(),
        reranker_enabled=False,
    )

    primary = candidates[0]
    secondary = candidates[1]

    for candidate in candidates:
        assert all(0.0 <= score <= 1.0 for score in candidate.normalized_scores.values())

    scoring_debug = primary.debug["scoring"]
    assert scoring_debug["reranker_redistributed"] is True
    assert scoring_debug["effective_weights"]["reranker"] == pytest.approx(0.0)
    assert scoring_debug["effective_weights"]["retrieval_fusion"] == pytest.approx(0.40)
    assert scoring_debug["effective_weights"]["vector"] == pytest.approx(0.22)
    assert scoring_debug["effective_weights"]["full_text"] == pytest.approx(0.16)
    assert scoring_debug["effective_weight_sum"] == pytest.approx(1.0)
    assert any(
        item == {
            "component": "user_rating",
            "reason": "not_provided",
            "excluded_from_denominator": True,
        }
        for item in scoring_debug["missing_components"]
    )
    assert "temporal" in scoring_debug["contributions"]
    assert primary.final_score > secondary.final_score


@pytest.mark.skipif(
    not arcade_runtime_available(),
    reason="arcadedb_embedded is required for phase 8 scoring integration tests",
)
def test_search_results_include_phase8_scoring_diagnostics(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)
    monkeypatch.setattr(service_module, "_utcnow", lambda: _NOW)

    scope = Scope(project_id="arcade")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        document_chunk = store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
                text="Alpha architecture document chunk.",
                embedding=[1.0, 0.0, 0.0, 0.0],
                importance=0.9,
                confidence=0.8,
                user_rating=1.0,
                source_path="docs/architecture.md",
            )
        )
        observation = store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.OBSERVATION,
                text="Alpha architecture observation.",
                embedding=[1.0, 0.0, 0.0, 0.0],
                importance=0.9,
                confidence=0.8,
            )
        )

        repository = store._service._repository()
        stale_observation = repository.get_memory(observation.memory_id)
        assert stale_observation is not None
        repository._persist_existing(
            stale_observation.model_copy(
                update={
                    "created_at": _NOW - timedelta(days=28),
                    "updated_at": _NOW - timedelta(days=28),
                }
            ),
            use_transaction=True,
        )

        results = store.search(MemorySearchQuery(scope=scope, text="alpha architecture", limit=5))
        by_memory_id = {result.memory.memory_id: result for result in results}

        document_result = by_memory_id[document_chunk.memory_id]
        observation_result = by_memory_id[observation.memory_id]

        assert document_result.component_scores["temporal"] == pytest.approx(1.0)
        assert observation_result.component_scores["temporal"] == pytest.approx(exp(-2.0))
        assert document_result.component_scores["importance"] == pytest.approx(0.9)
        assert document_result.component_scores["confidence"] == pytest.approx(0.8)
        assert document_result.component_scores["user_rating"] == pytest.approx(1.0)
        assert all(0.0 <= score <= 1.0 for score in document_result.normalized_scores.values())
        assert document_result.debug["scoring"]["reranker_redistributed"] is True
        assert document_result.debug["scoring"]["temporal"]["reason"] == "document_chunk_no_decay"
        assert (
            document_result.debug["scoring"]["effective_weights"]["retrieval_fusion"]
            == pytest.approx(0.40)
        )
        assert "importance" in document_result.debug["scoring"]["contributions"]
        assert "confidence" in document_result.debug["scoring"]["contributions"]
        assert "user_rating" in document_result.debug["scoring"]["contributions"]
    finally:
        store.close()