from __future__ import annotations

import pytest

from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.models import MemoryCreate, MemoryType, MemoryUpdate, Scope


if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 3 service tests", allow_module_level=True)


def test_memory_store_crud_upsert_stats_and_health(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        created = store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                stable_key="decision:service-api",
                memory_type=MemoryType.DECISION,
                title="Keep the store facade thin",
                summary="Phase 3 should route through the shared service layer.",
                text="MemoryStore delegates CRUD operations to MemoryService.",
            )
        )

        loaded = store.get_memory(created.memory_id)
        assert loaded is not None
        assert loaded.memory_id == created.memory_id
        assert loaded.scope.project_id == "arcade"

        updated = store.update_memory(
            created.memory_id,
            MemoryUpdate(summary="Updated summary", user_rating=0.9),
        )
        assert updated.version == 2
        assert updated.summary == "Updated summary"
        assert updated.updated_at >= created.updated_at

        upserted = store.upsert_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                stable_key="decision:service-api",
                memory_type=MemoryType.DECISION,
                title="Keep adapters thin",
                text="Stable-key upsert should update the existing record.",
            )
        )
        assert upserted.memory_id == created.memory_id
        assert upserted.version == 3
        assert upserted.title == "Keep adapters thin"

        global_record = store.add_memory(MemoryCreate(text="Global operational note."))
        assert global_record.scope.is_global is True

        stats = store.stats()
        assert stats.total_records == 2
        assert stats.scope_counts == {"global": 1, "scoped": 1}
        assert stats.status_counts == {"active": 2}
        assert stats.type_counts == {"decision": 1, "observation": 1}

        health = store.health()
        assert health.status == "ok"
        assert health.schema_version == 1
        assert health.database_path == tmp_path / "arcade"
    finally:
        store.close()