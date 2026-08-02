from __future__ import annotations

import json

import pytest

from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.models import InventoryDetailLevel, MemoryCreate, MemoryType, MemoryUpdate, Scope


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


def test_memory_store_inventory_projects_full_report_and_summary_filter(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
    )

    try:
        store.add_memory(MemoryCreate(text="Global operational note."))
        store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="alice", project_id="docs"),
                memory_type=MemoryType.PROJECT_FACT,
                source_path=str(tmp_path / "private" / "docs.md"),
                text="Docs architecture fact.",
            )
        )
        store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="bob", project_id="ops", agent_id="copilot"),
                memory_type=MemoryType.TASK_STATE,
                text="Ops task remains active.",
            )
        )
        store.add_memory(
            MemoryCreate(
                scope=Scope(project_id="docs"),
                memory_type=MemoryType.DOCUMENT_CHUNK,
                text="Chunk content for the docs project.",
                chunk_id="docs-0",
                document_chunk_index=0,
                section_index=0,
                section_chunk_index=0,
            )
        )

        full_report = store.inventory(
            detail=InventoryDetailLevel.FULL,
            include_names=True,
            names_limit=1,
        )

        assert full_report.detail == InventoryDetailLevel.FULL
        assert full_report.summary.total_records == 4
        assert full_report.summary.scope_counts == {"global": 1, "scoped": 3}
        assert full_report.summary.type_counts == {
            "document_chunk": 1,
            "observation": 1,
            "project_fact": 1,
            "task_state": 1,
        }

        assert full_report.scopes is not None
        assert full_report.generated_at.tzinfo is not None
        assert full_report.scopes.distinct_scope_tuples == 4
        assert full_report.scopes.global_records == 1
        assert full_report.scopes.scoped_records == 3
        assert full_report.scopes.user_ids.count == 2
        assert full_report.scopes.user_ids.names == ["alice"]
        assert full_report.scopes.user_ids.truncated is True
        assert full_report.scopes.user_ids.remaining == 1
        assert full_report.scopes.project_ids.count == 2
        assert full_report.scopes.project_ids.names == ["docs"]
        assert full_report.scopes.project_ids.truncated is True
        assert full_report.scopes.project_ids.remaining == 1
        assert full_report.scopes.agent_ids.count == 1
        assert full_report.scopes.agent_ids.names == ["copilot"]
        assert full_report.scopes.agent_ids.truncated is False
        assert full_report.scopes.agent_ids.remaining == 0

        type_map = {item.memory_type: item for item in full_report.memory_types}
        assert type_map[MemoryType.OBSERVATION].count == 1
        assert type_map[MemoryType.OBSERVATION].scope_counts == {"global": 1, "scoped": 0}
        assert type_map[MemoryType.PROJECT_FACT].count == 1
        assert type_map[MemoryType.PROJECT_FACT].scope_counts == {"global": 0, "scoped": 1}
        assert type_map[MemoryType.TASK_STATE].count == 1
        assert type_map[MemoryType.DOCUMENT_CHUNK].count == 1
        assert type_map[MemoryType.DECISION].count == 0

        serialized = json.dumps(full_report.model_dump(mode="json"))
        assert "Docs architecture fact." not in serialized
        assert "Chunk content for the docs project." not in serialized
        assert str(tmp_path / "private" / "docs.md") not in serialized
        assert '"source_path"' not in serialized
        assert '"embedding_model"' not in serialized

        filtered_summary = store.inventory(detail="summary", include_document_chunks=False)
        assert filtered_summary.detail == InventoryDetailLevel.SUMMARY
        assert filtered_summary.summary.total_records == 3
        assert filtered_summary.summary.scope_counts == {"global": 1, "scoped": 2}
        assert filtered_summary.summary.type_counts == {
            "observation": 1,
            "project_fact": 1,
            "task_state": 1,
        }
        assert filtered_summary.scopes is None
        assert filtered_summary.memory_types == []

        stats = store.stats()
        assert stats.model_dump(mode="python") == store.inventory(
            detail="summary"
        ).summary.model_dump(mode="python")
    finally:
        store.close()