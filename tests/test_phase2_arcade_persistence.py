from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memory_store.arcade import (
    ArcadeConnectionSettings,
    ArcadeMemoryRepository,
    arcade_runtime_available,
    ensure_database_schema,
    open_arcade_database,
    read_schema_version,
)
from memory_store.arcade.connection import ArcadeDatabaseHandle, ArcadeProcessLock, normalize_database_path
from memory_store.arcade.migrations import validate_schema_version
from memory_store.arcade.schema import (
    EDGE_TYPES,
    MEMORY_RECORD_VERTEX,
    SCHEMA_VERSION_VERTEX,
    required_edge_types,
    required_index_aliases,
    required_vertex_types,
)
from memory_store.arcade.transactions import batch_insert_memories, run_in_transaction
from memory_store.errors import PersistenceError, SchemaMismatchError
from memory_store.models import MemoryCreate, MemoryStatus, MemoryType, MemoryUpdate, Scope


if not arcade_runtime_available():
    pytest.skip("arcadedb_embedded is required for phase 2 persistence tests", allow_module_level=True)


def _open_repository(tmp_path: Path) -> tuple[object, ArcadeMemoryRepository]:
    handle = open_arcade_database(ArcadeConnectionSettings(path=tmp_path / "db", create_if_missing=True))
    ensure_database_schema(handle.database, expected_version=1, embedding_dimensions=4)
    return handle, ArcadeMemoryRepository(handle.database, schema_version=1)


def _lock_path_for(database_path: Path) -> Path:
    normalized = normalize_database_path(database_path)
    return normalized.parent / f"{normalized.name}.lock"


def test_database_initializes_schema_types_and_indexes(tmp_path: Path) -> None:
    handle, _ = _open_repository(tmp_path)

    try:
        assert handle.database.is_open() is True
        assert handle.database_path.exists() is True
        assert read_schema_version(handle.database) == 1

        for type_name in required_vertex_types():
            assert handle.database.schema.exists_type(type_name) is True

        for edge_type in required_edge_types():
            assert handle.database.schema.exists_type(edge_type) is True

        for index_name in required_index_aliases():
            assert handle.database.schema.exists_index(index_name) is True

        assert handle.database.schema.get_vector_index(MEMORY_RECORD_VERTEX, "embedding") is not None
    finally:
        handle.close()


def test_database_lock_is_removed_on_normal_close(tmp_path: Path) -> None:
    handle = open_arcade_database(ArcadeConnectionSettings(path=tmp_path / "db", create_if_missing=True))
    lock_path = _lock_path_for(tmp_path / "db")

    assert lock_path.exists() is True

    handle.close()

    assert lock_path.exists() is False


def test_database_handle_releases_lock_when_close_raises(tmp_path: Path) -> None:
    database_path = normalize_database_path(tmp_path / "db")
    lock = ArcadeProcessLock(_lock_path_for(database_path))
    lock.acquire()

    class FailingDatabase:
        def close(self) -> None:
            raise RuntimeError("synthetic close failure")

    handle = ArcadeDatabaseHandle(
        settings=ArcadeConnectionSettings(path=database_path),
        database_path=database_path,
        database=FailingDatabase(),
        _lock=lock,
    )

    with pytest.raises(PersistenceError, match="Failed to close ArcadeDB database"):
        handle.close()

    assert _lock_path_for(database_path).exists() is False


def test_arcade_process_lock_reclaims_stale_lock_without_opening_database(tmp_path: Path) -> None:
    lock_path = tmp_path / "db.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 0,
                "hostname": "stale-host",
                "created_at": "2026-07-02T00:00:00+00:00",
                "process_start": "stale-start",
            }
        ),
        encoding="utf-8",
    )

    lock = ArcadeProcessLock(lock_path)

    try:
        lock.acquire()
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["hostname"]
        assert payload["created_at"]
    finally:
        lock.release()

    assert lock_path.exists() is False


def test_arcade_process_lock_refuses_live_owner_without_opening_database(tmp_path: Path) -> None:
    lock_path = tmp_path / "db.lock"
    lock_path.write_text(f"pid={os.getpid()}\n", encoding="utf-8")

    lock = ArcadeProcessLock(lock_path)

    try:
        with pytest.raises(PersistenceError, match="already locked"):
            lock.acquire()
    finally:
        lock_path.unlink(missing_ok=True)


def test_open_arcade_database_releases_lock_when_open_fails(tmp_path: Path) -> None:
    database_path = tmp_path / "missing-db"

    with pytest.raises(PersistenceError, match="does not exist"):
        open_arcade_database(ArcadeConnectionSettings(path=database_path, create_if_missing=False))

    assert _lock_path_for(database_path).exists() is False


def test_open_arcade_database_reclaims_stale_lock(tmp_path: Path) -> None:
    database_path = normalize_database_path(tmp_path / "db")
    lock_path = _lock_path_for(database_path)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 0,
                "hostname": "stale-host",
                "created_at": "2026-07-02T00:00:00+00:00",
                "process_start": "stale-start",
            }
        ),
        encoding="utf-8",
    )

    handle = open_arcade_database(ArcadeConnectionSettings(path=database_path, create_if_missing=True))

    try:
        assert lock_path.exists() is True
    finally:
        handle.close()

    assert lock_path.exists() is False


def test_open_arcade_database_preserves_live_lock(tmp_path: Path) -> None:
    database_path = normalize_database_path(tmp_path / "db")
    lock_path = _lock_path_for(database_path)
    lock_path.write_text(f"pid={os.getpid()}\n", encoding="utf-8")

    try:
        with pytest.raises(PersistenceError, match="already locked"):
            open_arcade_database(ArcadeConnectionSettings(path=database_path, create_if_missing=True))
    finally:
        lock_path.unlink(missing_ok=True)


def test_schema_migrations_are_idempotent_and_validate_version(tmp_path: Path) -> None:
    handle, _ = _open_repository(tmp_path)

    try:
        ensure_database_schema(handle.database, expected_version=1, embedding_dimensions=4)
        count = handle.database.query(
            "sql", "SELECT count(*) as count FROM MigrationRecord"
        ).first()
        assert count is not None
        assert int(count.get("count")) == 1

        schema_vertex = handle.database.lookup_by_key(SCHEMA_VERSION_VERTEX, ["key"], ["active"])
        assert schema_vertex is not None

        with handle.database.transaction():
            mutable = schema_vertex.modify()
            mutable.set("version", 99)
            mutable.save()

        with pytest.raises(SchemaMismatchError):
            validate_schema_version(handle.database, expected_version=1)
    finally:
        handle.close()


def test_repository_insert_update_upsert_and_lookup_helpers(tmp_path: Path) -> None:
    handle, repository = _open_repository(tmp_path)

    try:
        created = repository.insert_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                stable_key="decision:reranker",
                memory_type=MemoryType.DECISION,
                title="Use FastEmbed reranker",
                summary="Initial retrieval decision",
                text="Use FastEmbed reranker after hybrid retrieval.",
                tags=["retrieval", "reranker"],
                source_hash="source-1",
                chunk_id="chunk-1",
                embedding=[0.1, 0.2, 0.3, 0.4],
                embedding_dim=4,
            )
        )

        loaded = repository.get_memory(created.memory_id)
        assert loaded is not None
        assert loaded.memory_id == created.memory_id
        assert loaded.scope.project_id == "arcade"

        updated = repository.update_memory(
            created.memory_id,
            MemoryUpdate(summary="Updated retrieval decision", user_rating=0.9),
        )
        assert updated.version == 2
        assert updated.summary == "Updated retrieval decision"
        assert updated.user_rating == pytest.approx(0.9)

        upserted = repository.upsert_memory(
            MemoryCreate(
                scope=Scope(project_id="arcade"),
                stable_key="decision:reranker",
                memory_type=MemoryType.DECISION,
                title="Use the reranker after fusion",
                text="Keep the reranker after reciprocal rank fusion.",
                source_hash="source-1",
                chunk_id="chunk-1",
            )
        )
        assert upserted.memory_id == created.memory_id
        assert upserted.version == 3
        assert upserted.title == "Use the reranker after fusion"

        by_scope = repository.list_by_scope(Scope(project_id="arcade"))
        by_chunk = repository.list_by_chunk_id("chunk-1")
        by_source = repository.list_by_source_hash("source-1")

        assert [record.memory_id for record in by_scope] == [created.memory_id]
        assert [record.memory_id for record in by_chunk] == [created.memory_id]
        assert [record.memory_id for record in by_source] == [created.memory_id]

        marked = repository.mark_status(created.memory_id, MemoryStatus.SUPERSEDED)
        assert marked.status == MemoryStatus.SUPERSEDED
        assert repository.count_memories(scope=Scope(project_id="arcade")) == 1
        assert repository.count_memories(status=MemoryStatus.SUPERSEDED) == 1
    finally:
        handle.close()


def test_repository_creates_edges_and_reads_one_hop_neighbors(tmp_path: Path) -> None:
    handle, repository = _open_repository(tmp_path)

    try:
        left = repository.insert_memory(
            MemoryCreate(scope=Scope(project_id="arcade"), text="ArcadeDB stores graph edges.")
        )
        right = repository.insert_memory(
            MemoryCreate(scope=Scope(project_id="arcade"), text="One-hop expansion should find me.")
        )

        repository.create_edge(left.memory_id, right.memory_id, "RELATED_TO", {"reason": "test"})
        neighbors = repository.read_one_hop_neighbors(left.memory_id, edge_types=("RELATED_TO",))

        assert [record.memory_id for record in neighbors] == [right.memory_id]
    finally:
        handle.close()


def test_transaction_rolls_back_failed_batch_insert(tmp_path: Path) -> None:
    handle, repository = _open_repository(tmp_path)

    try:
        first = MemoryCreate(scope=Scope(project_id="arcade"), text="Keep this atomic.")
        second = MemoryCreate(scope=Scope(project_id="arcade"), text="This should roll back too.")

        original_insert = repository._insert_memory

        def failing_insert(memory: MemoryCreate, *, use_transaction: bool):
            record = original_insert(memory, use_transaction=use_transaction)
            if memory.text == second.text:
                raise RuntimeError("synthetic failure")
            return record

        repository._insert_memory = failing_insert  # type: ignore[method-assign]

        with pytest.raises(PersistenceError):
            batch_insert_memories(repository, [first, second])

        assert repository.count_memories(scope=Scope(project_id="arcade")) == 0

        with pytest.raises(PersistenceError):
            run_in_transaction(handle.database, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    finally:
        handle.close()