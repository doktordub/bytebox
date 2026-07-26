from __future__ import annotations

import pytest

from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.errors import LifecycleError
from memory_store.models import (
    MemoryCreate,
    MemoryFeedback,
    MemoryStatus,
    MemoryType,
    Scope,
)
from memory_store.retrieval.filters import is_record_retrievable


if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 5 lifecycle tests", allow_module_level=True)


def test_promote_supersede_and_contradict_are_auditable(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        candidate = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                status=MemoryStatus.CANDIDATE,
                confidence=0.20,
                importance=0.35,
                text="Candidate memory that still needs confirmation.",
            )
        )

        promoted = store.promote(candidate.memory_id, reason="user-confirmed")
        assert promoted.status == MemoryStatus.ACTIVE
        assert promoted.confidence == pytest.approx(0.75)
        assert promoted.importance == pytest.approx(0.60)
        assert promoted.metadata["lifecycle_events"][-1]["operation"] == "promote"
        assert promoted.metadata["lifecycle_events"][-1]["reason"] == "user-confirmed"

        replacement = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                confidence=0.90,
                importance=0.80,
                text="Replacement memory with newer wording.",
            )
        )

        store.supersede(promoted.memory_id, replacement.memory_id, reason="newer source")
        superseded = store.get_memory(promoted.memory_id)
        current = store.get_memory(replacement.memory_id)
        assert superseded is not None
        assert current is not None
        assert superseded.status == MemoryStatus.SUPERSEDED
        assert superseded.superseded_by == replacement.memory_id
        assert superseded.allow_retrieval is False
        assert current.status == MemoryStatus.ACTIVE
        assert current.metadata["lifecycle_events"][-1]["related_memory_id"] == promoted.memory_id

        repository = store._service._repository()
        supersession_neighbors = repository.read_one_hop_neighbors(
            promoted.memory_id,
            edge_types=("SUPERSEDES",),
        )
        assert [neighbor.memory_id for neighbor in supersession_neighbors] == [replacement.memory_id]

        left = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                confidence=0.80,
                text="Feature flag is always enabled.",
            )
        )
        right = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                confidence=0.70,
                text="Feature flag is disabled in staging.",
            )
        )

        store.contradict(left.memory_id, right.memory_id, reason="conflicting reports")
        contradicted_left = store.get_memory(left.memory_id)
        contradicted_right = store.get_memory(right.memory_id)
        assert contradicted_left is not None
        assert contradicted_right is not None
        assert contradicted_left.status == MemoryStatus.CONTRADICTED
        assert contradicted_right.status == MemoryStatus.CONTRADICTED
        assert contradicted_left.confidence == pytest.approx(0.65)
        assert contradicted_right.confidence == pytest.approx(0.55)
        assert contradicted_left.metadata["lifecycle_events"][-1]["operation"] == "contradict"

        contradiction_neighbors = repository.read_one_hop_neighbors(
            left.memory_id,
            edge_types=("CONTRADICTS",),
        )
        assert [neighbor.memory_id for neighbor in contradiction_neighbors] == [right.memory_id]
    finally:
        store.close()


def test_expire_forget_and_feedback_update_scores_and_filters(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        expiring = store.add_memory(MemoryCreate(text="Transient operational note."))
        store.expire(expiring.memory_id, reason="stale data")
        expired = store.get_memory(expiring.memory_id)
        assert expired is not None
        assert expired.status == MemoryStatus.EXPIRED
        assert expired.allow_retrieval is False
        assert expired.expires_at is not None
        assert is_record_retrievable(expired) is False

        forgetting = store.add_memory(MemoryCreate(text="Sensitive note to forget."))
        store.forget(forgetting.memory_id)
        forgotten = store.get_memory(forgetting.memory_id)
        assert forgotten is not None
        assert forgotten.status == MemoryStatus.FORGOTTEN
        assert forgotten.allow_retrieval is False
        assert forgotten.allow_llm_context is False
        assert forgotten.metadata["lifecycle_events"][-1]["operation"] == "forget"
        assert is_record_retrievable(forgotten) is False

        target = store.add_memory(
            MemoryCreate(
                text="Fact with room for more confidence.",
                confidence=0.35,
                importance=0.40,
            )
        )
        updated = store.add_feedback(
            target.memory_id,
            MemoryFeedback(
                confirmed=True,
                confidence=0.90,
                importance=0.80,
                user_rating=1.0,
                note="User explicitly confirmed this memory.",
            ),
        )
        assert updated.confidence == pytest.approx(0.90)
        assert updated.importance == pytest.approx(0.80)
        assert updated.user_rating == pytest.approx(1.0)
        assert updated.metadata["feedback_events"][-1]["confirmed"] is True
        assert updated.metadata["feedback_events"][-1]["note"] == "User explicitly confirmed this memory."
    finally:
        store.close()


def test_document_chunks_are_rejected_by_agent_lifecycle_methods(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        document_chunk = store.add_memory(
            MemoryCreate(
                memory_type=MemoryType.DOCUMENT_CHUNK,
                text="Chunked markdown content.",
                source_path="docs/architecture.md",
            )
        )
        agent_memory = store.add_memory(MemoryCreate(text="Mutable agent memory."))

        operations = [
            lambda: store.promote(document_chunk.memory_id),
            lambda: store.expire(document_chunk.memory_id),
            lambda: store.forget(document_chunk.memory_id),
            lambda: store.add_feedback(document_chunk.memory_id, MemoryFeedback(positive=True)),
            lambda: store.supersede(document_chunk.memory_id, agent_memory.memory_id),
            lambda: store.supersede(agent_memory.memory_id, document_chunk.memory_id),
            lambda: store.contradict(document_chunk.memory_id, agent_memory.memory_id),
        ]

        for operation in operations:
            with pytest.raises(LifecycleError):
                operation()
    finally:
        store.close()