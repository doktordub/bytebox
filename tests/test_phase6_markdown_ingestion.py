from __future__ import annotations

from datetime import datetime, timezone

import pytest

import memory_store.service as service_module
from memory_store import MemoryStore
from memory_store.arcade import arcade_runtime_available
from memory_store.embeddings.fastembed_provider import EmbeddedText
from memory_store.errors import IngestionError, PersistenceError
from memory_store.ingestion.chunking import chunk_markdown_sections
from memory_store.ingestion.frontmatter import parse_frontmatter
from memory_store.ingestion.hashing import (
    compute_chunk_id,
    compute_content_hash,
    compute_source_hash,
)
from memory_store.ingestion.markdown import read_markdown_file
from memory_store.models import MemoryCreate, MemoryStatus, MemoryType, Scope

if not arcade_runtime_available():
    pytest.skip(
        "arcadedb_embedded is required for phase 6 ingestion tests", allow_module_level=True
    )


class _FakeProvider:
    seen_texts: list[str] = []
    seen_batches: list[list[str]] = []

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
        type(self).seen_batches.append(list(texts))
        return [self.embed_text(text) for text in texts]


def test_parse_frontmatter_validates_supported_fields() -> None:
    frontmatter, body = parse_frontmatter(
        """---
name: Architecture
description: Deterministic ingestion
tags:
  - python
  - retrieval
version: \"0.1\"
owner: docs-team
priority: high
---
# Title
Body
"""
    )

    assert frontmatter["name"] == "Architecture"
    assert frontmatter["tags"] == ["python", "retrieval"]
    assert frontmatter["priority"] == "high"
    assert body.startswith("# Title")

    with pytest.raises(IngestionError):
        parse_frontmatter("---\nname: " + ("x" * 1025) + "\n---\nBody")


def test_chunking_preserves_code_blocks_and_heading_paths(tmp_path) -> None:
    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Example

Intro paragraph with enough words to overflow the small token budget used by this test.

```python
print('hello')
print('world')
```

Closing paragraph that should remain chunked deterministically.
""",
        encoding="utf-8",
    )

    document = read_markdown_file(path)
    chunks = chunk_markdown_sections(
        document.sections,
        max_tokens=12,
        overlap_tokens=2,
        include_heading_path=True,
        preserve_code_blocks=True,
    )

    assert any(chunk.heading_path == ("Architecture", "Example") for chunk in chunks)
    code_chunks = [chunk.text for chunk in chunks if "```python" in chunk.text]
    assert code_chunks == ["```python\nprint('hello')\nprint('world')\n```"]
    assert all(chunk.approximate_token_count == len(chunk.text.split()) for chunk in chunks)


def test_chunking_max_tokens_is_an_approximate_budget_for_large_paragraphs() -> None:
    sections = [
        type(
            "Section",
            (),
            {
                "heading_path": ("Architecture", "Overview"),
                "section_index": 0,
                "text": (
                    "This paragraph stays intact because the chunker does not split within "
                    "a paragraph even when the approximate token budget is smaller than "
                    "the paragraph itself."
                ),
            },
        )()
    ]

    chunks = chunk_markdown_sections(
        sections,
        max_tokens=10,
        overlap_tokens=2,
        include_heading_path=True,
        preserve_code_blocks=True,
    )

    assert len(chunks) == 1
    assert chunks[0].text == sections[0].text
    assert chunks[0].approximate_token_count > 10


def test_chunking_overlap_repeats_tail_tokens_between_chunks() -> None:
    sections = [
        type(
            "Section",
            (),
            {
                "heading_path": ("Architecture", "Overview"),
                "section_index": 0,
                "text": (
                    "Alpha beta gamma delta epsilon zeta eta theta iota kappa.\n\n"
                    "Lambda mu nu xi omicron pi rho sigma tau upsilon."
                ),
            },
        )()
    ]

    chunks = chunk_markdown_sections(
        sections,
        max_tokens=10,
        overlap_tokens=3,
        include_heading_path=True,
        preserve_code_blocks=True,
    )

    assert len(chunks) == 2
    assert chunks[0].text == "Alpha beta gamma delta epsilon zeta eta theta iota kappa."
    assert chunks[1].text.startswith("theta iota kappa.")
    assert "Lambda mu nu xi omicron pi rho sigma tau upsilon." in chunks[1].text
    assert chunks[1].approximate_token_count > 10


def test_hashes_and_chunk_ids_are_deterministic() -> None:
    source_hash = compute_source_hash("docs/architecture.md", ("Backend",), 0)
    content_hash = compute_content_hash("Chunk content")
    chunk_id = compute_chunk_id("docs/architecture.md", ("Backend",), 0, content_hash)

    assert source_hash == compute_source_hash("docs/architecture.md", ("Backend",), 0)
    assert content_hash == compute_content_hash("Chunk content")
    assert chunk_id == compute_chunk_id("docs/architecture.md", ("Backend",), 0, content_hash)
    assert (
        compute_chunk_id("docs/architecture.md", ("Backend",), 0, compute_content_hash("changed"))
        != chunk_id
    )


def test_ingest_document_promotes_first_class_chunk_position_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Alpha

Alpha section body.

## Beta

Beta section body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        store.ingest_document(path, scope)

        repository = store._service._repository()
        records = sorted(
            repository.list_by_source_path(
                path.resolve().as_posix(),
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
            ),
            key=lambda record: int(record.document_chunk_index or 0),
        )

        assert [tuple(record.heading_path or ()) for record in records] == [
            ("Architecture", "Alpha"),
            ("Architecture", "Beta"),
        ]
        assert [record.section_index for record in records] == [
            int(records[0].metadata["section_index"]),
            int(records[1].metadata["section_index"]),
        ]
        assert [record.section_chunk_index for record in records] == [0, 0]
        assert [record.document_chunk_index for record in records] == [0, 1]
        assert [record.chunk_index for record in records] == [0, 0]
        assert [record.metadata["section_chunk_index"] for record in records] == [0, 0]
        assert [record.metadata["document_chunk_index"] for record in records] == [0, 1]
        assert all(record.metadata["chunking_max_tokens"] == 350 for record in records)
        assert all(record.metadata["chunking_overlap_tokens"] == 50 for record in records)
        assert all(
            record.metadata["chunking_max_tokens_is_approximate"] is True for record in records
        )
        assert all(record.metadata["approximate_token_count"] >= 3 for record in records)
        assert records[0].section_index != records[1].section_index
    finally:
        store.close()


def test_existing_document_chunks_are_backfilled_with_first_class_position_fields(tmp_path) -> None:
    scope = Scope(project_id="docs")
    source_path = "docs/architecture.md"

    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        repository = store._service._repository()
        legacy_records: list[str] = []

        for section_index, heading in enumerate(("Alpha", "Beta")):
            legacy_memory = MemoryCreate(
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
                stable_key=f"legacy:{heading.lower()}",
                text=f"{heading} section body.",
                source_path=source_path,
                source_hash=f"hash-{heading.lower()}",
                chunk_id=f"chunk-{heading.lower()}",
                heading_path=["Architecture", heading],
                chunk_index=0,
                metadata={
                    "document_lifecycle": "source_controlled",
                    "section_index": section_index,
                },
            )
            record = repository._new_record(legacy_memory)
            payload = repository._serialize_record(record)
            payload.pop("section_index", None)
            payload.pop("section_chunk_index", None)
            payload.pop("document_chunk_index", None)

            with repository.database.transaction():
                vertex = repository.database.new_vertex("MemoryRecord")
                for key, value in payload.items():
                    vertex.set(key, value)
                vertex.save()

            legacy_records.append(record.memory_id)
    finally:
        store.close()

    reopened = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        repository = reopened._service._repository()
        records = sorted(
            repository.list_by_source_path(
                source_path,
                scope=scope,
                memory_type=MemoryType.DOCUMENT_CHUNK,
            ),
            key=lambda record: int(record.document_chunk_index or 0),
        )

        assert [record.memory_id for record in records] == legacy_records
        assert [record.section_index for record in records] == [0, 1]
        assert [record.section_chunk_index for record in records] == [0, 0]
        assert [record.chunk_index for record in records] == [0, 0]
        assert [record.document_chunk_index for record in records] == [0, 1]
        assert [record.metadata["document_chunk_index"] for record in records] == [0, 1]
    finally:
        reopened.close()


def test_ingest_document_handles_insert_skip_update_new_and_removed(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_batches.clear()
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """---
name: Architecture
description: Core system design
tags:
  - docs
  - memory
---
# Architecture

## Alpha

Alpha section body.

## Beta

Beta section body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model", "model_version": "stub/revision"},
    )

    try:
        first = store.ingest_document(path, scope)
        assert first.added == 2
        assert first.updated == 0
        assert first.removed == 0
        assert first.unchanged == 0
        assert len(_FakeProvider.seen_texts) == 2

        repository = store._service._repository()
        records = sorted(
            repository.list_by_source_path(
                path.resolve().as_posix(), scope=scope, memory_type=MemoryType.DOCUMENT_CHUNK
            ),
            key=lambda record: record.source_hash or "",
        )
        assert len(records) == 2
        original_by_heading = {tuple(record.heading_path or ()): record for record in records}

        second = store.ingest_document(path, scope)
        assert second.added == 0
        assert second.updated == 0
        assert second.removed == 0
        assert second.unchanged == 2
        assert len(_FakeProvider.seen_texts) == 2

        path.write_text(
            """---
name: Architecture
description: Core system design revised
tags:
  - docs
  - memory
---
# Architecture

## Alpha

Alpha section body changed.

## Gamma

Gamma section body.
""",
            encoding="utf-8",
        )

        third = store.ingest_document(path, scope)
        assert third.added == 1
        assert third.updated == 1
        assert third.removed == 1
        assert third.unchanged == 0
        assert len(_FakeProvider.seen_texts) == 4

        updated_records = repository.list_by_source_path(
            path.resolve().as_posix(), scope=scope, memory_type=MemoryType.DOCUMENT_CHUNK
        )
        by_heading = {tuple(record.heading_path or ()): record for record in updated_records}

        alpha_before = original_by_heading[("Architecture", "Alpha")]
        alpha_after = by_heading[("Architecture", "Alpha")]
        assert alpha_after.memory_id == alpha_before.memory_id
        assert alpha_after.version == 2
        assert alpha_after.chunk_id != alpha_before.chunk_id
        assert alpha_after.summary == "Core system design revised"

        gamma = by_heading[("Architecture", "Gamma")]
        assert gamma.version == 1
        assert gamma.embedding_model == "stub-model"

        beta = by_heading[("Architecture", "Beta")]
        assert beta.status == MemoryStatus.REMOVED
        assert beta.allow_retrieval is False
        assert beta.allow_llm_context is False
    finally:
        store.close()


def test_removed_chunks_can_be_hard_deleted(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_batches.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Alpha

Alpha section body.

## Beta

Beta section body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        chunking={"removed_chunk_policy": "hard_delete"},
    )

    try:
        store.ingest_document(path, scope)
        path.write_text(
            """# Architecture

## Alpha

Alpha section body.
""",
            encoding="utf-8",
        )

        result = store.ingest_document(path, scope)
        repository = store._service._repository()
        records = repository.list_by_source_path(
            path.resolve().as_posix(), scope=scope, memory_type=MemoryType.DOCUMENT_CHUNK
        )

        assert result.removed == 1
        assert len(records) == 1
        assert tuple(records[0].heading_path or ()) == ("Architecture", "Alpha")
    finally:
        store.close()


def test_ingest_folder_aggregates_file_results(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_batches.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\n## One\n\nFirst.", encoding="utf-8")
    (docs / "b.markdown").write_text("# B\n\n## Two\n\nSecond.", encoding="utf-8")

    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        result = store.ingest_folder(docs, Scope(project_id="docs"))
        assert result.files_processed == 2
        assert result.added == 2
        assert result.updated == 0
        assert result.removed == 0
        assert result.unchanged == 0
    finally:
        store.close()


def test_ingest_document_without_description_frontmatter_persists(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_batches.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Overview

This document intentionally omits frontmatter description.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        result = store.ingest_document(path, scope)
        assert result.ok is True
        assert result.added == 1

        repository = store._service._repository()
        records = repository.list_by_source_path(
            path.resolve().as_posix(),
            scope=scope,
            memory_type=MemoryType.DOCUMENT_CHUNK,
        )

        assert len(records) == 1
        assert records[0].title == "Architecture"
        assert records[0].summary is None
    finally:
        store.close()


def test_ingest_document_rolls_back_failed_batch(monkeypatch, tmp_path) -> None:
    _FakeProvider.seen_batches.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## First

Keep this atomic.

## Second

This insert should fail.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        repository = store._service._repository()
        original_insert = repository._insert_memory

        def failing_insert(memory, *, use_transaction: bool):
            if tuple(memory.heading_path or ()) == ("Architecture", "Second"):
                raise RuntimeError("synthetic failure")
            return original_insert(memory, use_transaction=use_transaction)

        repository._insert_memory = failing_insert  # type: ignore[method-assign]

        with pytest.raises(PersistenceError):
            store.ingest_document(path, scope)

        assert repository.count_memories(scope=scope) == 0
    finally:
        store.close()


def test_ingest_document_batches_embeddings_and_bounds_transactions(
    monkeypatch,
    tmp_path,
) -> None:
    _FakeProvider.seen_batches.clear()
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    transaction_calls = 0
    original_run_in_transaction = service_module.run_in_transaction

    def recording_run_in_transaction(database, operation):
        nonlocal transaction_calls
        transaction_calls += 1
        return original_run_in_transaction(database, operation)

    monkeypatch.setattr(service_module, "run_in_transaction", recording_run_in_transaction)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## One

First section body.

## Two

Second section body.

## Three

Third section body.

## Four

Fourth section body.

## Five

Fifth section body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model", "batch_size": 4},
        ingestion={"max_chunks_per_document_batch": 2, "max_chunks_per_transaction": 2},
    )

    try:
        result = store.ingest_document(path, scope)

        assert result.added == 5
        assert result.updated == 0
        assert result.removed == 0
        assert [len(batch) for batch in _FakeProvider.seen_batches] == [2, 2, 1]
        assert transaction_calls == 3

        repository = store._service._repository()
        assert repository.count_memories(scope=scope) == 5
    finally:
        store.close()


def test_ingest_document_dry_run_reports_preflight_and_skips_persistence(
    monkeypatch,
    tmp_path,
) -> None:
    _FakeProvider.seen_batches.clear()
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """---
name: Architecture
tags:
  - docs
labels:
  owner: docs-team
  priority: high
reviewers:
  - name: Ada
---
# Architecture

Body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        result = store.ingest_document(path, scope, dry_run=True)

        assert result.diagnostics.dry_run is True
        assert result.diagnostics.file_size_bytes > 0
        assert result.diagnostics.frontmatter_bytes > 0
        assert result.diagnostics.frontmatter_keys == ["name", "tags", "labels", "reviewers"]
        assert result.diagnostics.dropped_metadata_fields == ["reviewers"]
        assert result.counters.section_count == 1
        assert result.counters.chunk_count == 1
        assert result.added == 1
        assert result.timings.embed_ms == 0
        assert result.timings.persist_ms == 0
        assert _FakeProvider.seen_batches == []
        assert store._service._repository().count_memories(scope=scope) == 0
    finally:
        store.close()


def test_ingest_document_rejects_documents_that_exceed_preflight_limits(
    monkeypatch,
    tmp_path,
) -> None:
    _FakeProvider.seen_batches.clear()
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """# Architecture

## Alpha

Alpha section body.

## Beta

Beta section body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        ingestion={"max_chunks": 1},
    )

    try:
        with pytest.raises(IngestionError) as exc_info:
            store.ingest_document(path, scope)

        assert _FakeProvider.seen_batches == []
        assert getattr(exc_info.value, "ingest_diagnostics")["limit_violations"] == [
            "chunk_count=2 exceeds max_chunks=1"
        ]
        assert store._service._repository().count_memories(scope=scope) == 0
    finally:
        store.close()


def test_ingest_document_flattens_supported_frontmatter_before_persistence(
    monkeypatch,
    tmp_path,
) -> None:
    _FakeProvider.seen_batches.clear()
    _FakeProvider.seen_texts.clear()
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    path = tmp_path / "architecture.md"
    path.write_text(
        """---
name: Architecture
labels:
  owner: docs-team
  flags:
    - internal
    - docs
reviewers:
  - name: Ada
---
# Architecture

Body.
""",
        encoding="utf-8",
    )

    scope = Scope(project_id="docs")
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
    )

    try:
        result = store.ingest_document(path, scope)

        record = store._service._repository().list_by_source_path(
            path.resolve().as_posix(),
            scope=scope,
            memory_type=MemoryType.DOCUMENT_CHUNK,
        )[0]

        assert result.diagnostics.dropped_metadata_fields == ["reviewers"]
        assert record.metadata["frontmatter"] == {
            "name": "Architecture",
            "labels.owner": "docs-team",
            "labels.flags": ["internal", "docs"],
        }
        assert record.metadata["frontmatter_dropped_fields"] == ["reviewers"]
    finally:
        store.close()
