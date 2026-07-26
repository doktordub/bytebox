from __future__ import annotations

import pytest

from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.arcade.schema import (
    IMPORT_EXPORT_AUDIT_VERTEX,
    REDACTION_AUDIT_VERTEX,
)
from memory_store.models import (
    MemoryCreate,
    MemoryImport,
    MemorySearchQuery,
    MemoryStatus,
    MemoryType,
    Scope,
    SensitivityLevel,
)

if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 9 privacy tests", allow_module_level=True)


def test_bulk_privacy_controls_support_partial_scope_operations(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        user_one_project_a = store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="user-1", project_id="project-a"),
                text="First scoped memory.",
            )
        )
        user_one_project_b = store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="user-1", project_id="project-b"),
                text="Second scoped memory.",
            )
        )
        untouched = store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="user-2", project_id="project-a"),
                text="Other user memory.",
            )
        )

        assert store.disable_memory(Scope(user_id="user-1")) == 2
        assert store.get_memory(user_one_project_a.memory_id).allow_retrieval is False
        assert store.get_memory(user_one_project_b.memory_id).allow_retrieval is False
        assert store.get_memory(untouched.memory_id).allow_retrieval is True

        assert store.forget_by_user("user-1") == 2
        assert store.get_memory(user_one_project_a.memory_id).status == MemoryStatus.FORGOTTEN
        assert store.get_memory(user_one_project_b.memory_id).allow_llm_context is False
        assert store.get_memory(untouched.memory_id).status == MemoryStatus.ACTIVE

        soft_deleted = store.add_memory(
            MemoryCreate(
                scope=Scope(agent_id="agent-1", project_id="project-delete"),
                text="Soft delete target.",
            )
        )
        preserved = store.add_memory(
            MemoryCreate(
                scope=Scope(agent_id="agent-2", project_id="project-delete"),
                text="Preserved target.",
            )
        )

        assert store.delete_by_scope(Scope(agent_id="agent-1")) == 1
        deleted_record = store.get_memory(soft_deleted.memory_id)
        assert deleted_record is not None
        assert deleted_record.status == MemoryStatus.DELETED
        assert deleted_record.allow_retrieval is False
        assert store.get_memory(preserved.memory_id).status == MemoryStatus.ACTIVE

        hard_deleted = store.add_memory(
            MemoryCreate(
                scope=Scope(agent_id="agent-hard", project_id="project-delete"),
                text="Hard delete target.",
            )
        )
        assert store.delete_by_scope(Scope(agent_id="agent-hard"), hard_delete=True) == 1
        assert store.get_memory(hard_deleted.memory_id) is None
    finally:
        store.close()


def test_export_import_and_redaction_are_safe_and_auditable(tmp_path) -> None:
    source_store = MemoryStore.from_config(
        database={"path": tmp_path / "source", "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )
    target_store = MemoryStore.from_config(
        database={"path": tmp_path / "target", "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        exported_one = source_store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="portable-user", project_id="portable-project"),
                stable_key="portable-user:alpha",
                text="alpha@example.com should be portable",
                metadata={"owner": "alpha@example.com"},
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        source_store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="portable-user", project_id="portable-project"),
                stable_key="portable-user:beta",
                text="Portable project memory.",
            )
        )
        source_store.add_memory(
            MemoryCreate(
                scope=Scope(user_id="other-user", project_id="portable-project"),
                text="Outside the user export.",
            )
        )

        exported_user_records = source_store.export_user_memories("portable-user")
        assert {record.user_id for record in exported_user_records} == {"portable-user"}
        assert len(exported_user_records) == 2

        scoped_export = source_store.export_scope(Scope(project_id="portable-project"))
        assert scoped_export.scope == Scope(project_id="portable-project")
        assert len(scoped_export.records) == 3

        import_result = target_store.import_memories(
            MemoryImport(records=exported_user_records, source="source-store"),
            mode="upsert",
        )
        assert import_result.inserted == 2
        assert import_result.updated == 0
        assert import_result.skipped == 0

        exported_record = next(
            record for record in exported_user_records if record.memory_id == exported_one.memory_id
        )
        updated_payload = exported_record.model_copy(
            update={"text": "Updated imported text with alpha@example.com"}
        )
        upsert_result = target_store.import_memories(
            MemoryImport(records=[updated_payload], source="source-store"),
            mode="upsert",
        )
        assert upsert_result.inserted == 0
        assert upsert_result.updated == 1

        conflict_result = target_store.import_memories(
            MemoryImport(records=[updated_payload], source="source-store"),
            mode="insert",
        )
        assert conflict_result.skipped == 1
        assert conflict_result.errors

        redaction = target_store.redact(
            [r"alpha@example\.com"],
            scope=Scope(user_id="portable-user"),
        )
        assert redaction.redacted == 1
        assert redaction.memory_ids == [exported_one.memory_id]

        redacted_record = target_store.get_memory(exported_one.memory_id)
        assert redacted_record is not None
        assert "alpha@example.com" not in redacted_record.text
        assert "[REDACTED]" in redacted_record.text
        assert redacted_record.metadata["owner"] == "[REDACTED]"
        assert redacted_record.embedding is None

        repository = target_store._service._repository()
        redaction_audit = repository.database.query(
            "sql",
            f"SELECT FROM {REDACTION_AUDIT_VERTEX} WHERE operation = ? LIMIT 1",
            "redact",
        ).first()
        assert redaction_audit is not None
        assert "alpha@example.com" not in str(redaction_audit.to_dict())

        import_export_audits = list(
            repository.database.query(
                "sql",
                (
                    f"SELECT FROM {IMPORT_EXPORT_AUDIT_VERTEX} "
                    "WHERE operation IN ['export_user_memories', 'export_scope', "
                    "'import_memories']"
                ),
            )
        )
        assert len(import_export_audits) >= 3
    finally:
        source_store.close()
        target_store.close()


def test_search_respects_sensitive_and_audit_visibility_flags(tmp_path) -> None:
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        scope = Scope(project_id="search-project")
        active = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Alpha public note.",
            )
        )
        sensitive = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Alpha sensitive note.",
                sensitivity=SensitivityLevel.SENSITIVE,
            )
        )
        removed = store.add_memory(
            MemoryCreate(
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
                status=MemoryStatus.REMOVED,
                text="Alpha removed chunk.",
                allow_retrieval=False,
                allow_llm_context=False,
            )
        )
        forgotten = store.add_memory(
            MemoryCreate(
                scope=scope,
                text="Alpha forgotten note.",
            )
        )
        store.forget(forgotten.memory_id)

        default_results = store.search(MemorySearchQuery(scope=scope, text="alpha"))
        assert [result.memory.memory_id for result in default_results] == [active.memory_id]

        sensitive_results = store.search(
            MemorySearchQuery(scope=scope, text="alpha", sensitivity=SensitivityLevel.SENSITIVE)
        )
        assert [result.memory.memory_id for result in sensitive_results] == [sensitive.memory_id]

        audit_results = store.search(
            MemorySearchQuery(
                scope=scope,
                text="alpha",
                include_removed=True,
                include_forgotten=True,
                allow_retrieval_only=False,
            )
        )
        audit_ids = {result.memory.memory_id for result in audit_results}
        assert {active.memory_id, removed.memory_id, forgotten.memory_id}.issubset(audit_ids)
        assert sensitive.memory_id not in audit_ids
    finally:
        store.close()