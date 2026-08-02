from __future__ import annotations

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import memory_store.service as service_module
from evals.metrics import (
    aggregate_metrics,
    duplicate_rate,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
    stale_memory_rate,
)
from evals.runner import load_golden_queries, run
from memory_store import MemoryStore
from memory_store.embeddings.fastembed_provider import EmbeddedText
from memory_store.models import (
    ChunkContextResponse,
    ChunkSearchResult,
    MemoryRecord,
    MemorySearchResult,
    MemoryStatus,
    MemoryType,
    Scope,
)


def _record(memory_id: str, **overrides: Any) -> MemoryRecord:
    now = datetime.now(timezone.utc)
    payload = {
        "memory_id": memory_id,
        "scope": Scope(project_id="arcade"),
        "stable_key": f"stable:{memory_id}",
        "title": f"Title for {memory_id}",
        "summary": f"Summary for {memory_id}",
        "text": f"Body for {memory_id}",
        "memory_type": MemoryType.DECISION,
        "status": MemoryStatus.ACTIVE,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    return MemoryRecord(**payload)


def _result(
    memory: MemoryRecord,
    *,
    final_score: float,
    component_scores: dict[str, float],
    normalized_scores: dict[str, float],
    debug: dict[str, Any] | None = None,
) -> MemorySearchResult:
    return MemorySearchResult(
        memory=memory,
        final_score=final_score,
        component_scores=component_scores,
        normalized_scores=normalized_scores,
        debug=debug or {},
    )


class FakeEvalStore:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    def search(self, query: Any) -> list[MemorySearchResult]:
        self.queries.append(query.text)
        if "reranking" in query.text.lower():
            return [
                _result(
                    _record("decision_fastembed_reranker"),
                    final_score=0.93,
                    component_scores={"full_text": 0.9, "retrieval_fusion": 0.8, "reranker": 0.95},
                    normalized_scores={
                        "full_text": 0.9,
                        "retrieval_fusion": 0.8,
                        "reranker": 0.95,
                    },
                    debug={
                        "rerank_applied": True,
                        "rerank_duration_ms": 7.5,
                        "rerank_input_size": 3,
                    },
                )
            ]

        return [
            _result(
                _record(
                    "chunk_bb1_architecture",
                    memory_type=MemoryType.DOCUMENT_CHUNK,
                    stable_key="doc:bb1:chunk-1",
                ),
                final_score=0.88,
                component_scores={"full_text": 0.88, "retrieval_fusion": 0.7},
                normalized_scores={"full_text": 0.88, "retrieval_fusion": 0.7},
                debug={"sources": ["full_text"]},
            ),
            _result(
                _record(
                    "chunk_bb1_architecture_duplicate",
                    memory_type=MemoryType.DOCUMENT_CHUNK,
                    stable_key="doc:bb1:chunk-1",
                ),
                final_score=0.52,
                component_scores={"full_text": 0.52},
                normalized_scores={"full_text": 0.52},
                debug={"sources": ["graph"]},
            ),
            _result(
                _record(
                    "chunk_stale_note",
                    memory_type=MemoryType.DOCUMENT_CHUNK,
                    status=MemoryStatus.SUPERSEDED,
                ),
                final_score=0.22,
                component_scores={"full_text": 0.22},
                normalized_scores={"full_text": 0.22},
                debug={},
            ),
        ]

    def search_document_chunks(
        self, *, text: str, scope: Scope, limit: int = 10
    ) -> list[ChunkSearchResult]:
        self.queries.append(f"chunk:{text}")
        return [
            ChunkSearchResult(
                memory_id="chunk-memory-1",
                chunk_id="chunk-deployment-1",
                source_path="docs/architecture.md",
                source_hash="hash-chunk-deployment-1",
                title="Architecture",
                summary="Chunk retrieval fixture",
                text="Deployment chunk body.",
                tags=["docs", "architecture"],
                heading_path=["Architecture", "Deployment"],
                section_index=1,
                section_chunk_index=0,
                document_chunk_index=1,
                final_score=0.89,
                component_scores={"retrieval_fusion": 0.89},
                normalized_scores={"retrieval_fusion": 0.89},
                debug={"rerank_input_size": 2},
                metadata={"approximate_token_count": 12},
            )
        ]

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        return ChunkContextResponse(
            chunk=ChunkSearchResult(
                memory_id="chunk-memory-1",
                chunk_id=chunk_id,
                source_path="docs/architecture.md",
                source_hash="hash-chunk-deployment-1",
                title="Architecture",
                summary="Chunk retrieval fixture",
                text="Deployment chunk body.",
                tags=["docs", "architecture"],
                heading_path=["Architecture", "Deployment"],
                section_index=1,
                section_chunk_index=0,
                document_chunk_index=1,
                final_score=1.0,
                metadata={"approximate_token_count": 12},
            ),
            before=[
                ChunkSearchResult(
                    memory_id="chunk-memory-0",
                    chunk_id="chunk-overview-0",
                    source_path="docs/architecture.md",
                    source_hash="hash-chunk-overview-0",
                    title="Architecture",
                    summary="Chunk retrieval fixture",
                    text="Overview chunk body.",
                    tags=["docs", "architecture"],
                    heading_path=["Architecture", "Overview"],
                    section_index=0,
                    section_chunk_index=0,
                    document_chunk_index=0,
                )
            ][: before or None],
            after=[
                ChunkSearchResult(
                    memory_id="chunk-memory-2",
                    chunk_id="chunk-rollout-2",
                    source_path="docs/architecture.md",
                    source_hash="hash-chunk-rollout-2",
                    title="Architecture",
                    summary="Chunk retrieval fixture",
                    text="Rollout chunk body.",
                    tags=["docs", "architecture"],
                    heading_path=["Architecture", "Rollout"],
                    section_index=2,
                    section_chunk_index=0,
                    document_chunk_index=2,
                )
            ][: after or None],
        )

    def close(self) -> None:
        self.closed = True


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
        if "alpha" in lowered or "reranking" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        else:
            vector = [0.0, 1.0, 0.0, 0.0]
        return EmbeddedText(
            vector=vector,
            model=self.model,
            model_version=self.model_version,
            dim=4,
            created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddedText]:
        return [self.embed_text(text) for text in texts]


class FakePythonExampleStore:
    def __init__(self) -> None:
        self.record = _record("decision_fastembed_reranker")
        self.closed = False

    def add_memory(self, memory: Any, *, embed: bool = False) -> MemoryRecord:
        return self.record.model_copy(update={"text": memory.text, "title": memory.title})

    def search(self, _query: Any) -> list[MemorySearchResult]:
        return [
            _result(
                self.record,
                final_score=0.91,
                component_scores={"full_text": 0.91},
                normalized_scores={"full_text": 0.91},
            )
        ]

    def add_feedback(self, memory_id: str, _feedback: Any) -> MemoryRecord:
        return self.record.model_copy(update={"memory_id": memory_id, "confidence": 0.95})

    def stats(self) -> Any:
        from memory_store.models import MemoryStats

        return MemoryStats(
            total_records=2,
            scope_counts={"global": 0, "scoped": 2},
            status_counts={"active": 2},
            type_counts={"decision": 1, "project_fact": 1},
        )

    def inventory(
        self,
        *,
        detail: str = "summary",
        include_names: bool = False,
        names_limit: int = 100,
        include_document_chunks: bool = True,
    ) -> Any:
        from memory_store.models import InventoryDetailLevel, MemoryInventoryReport

        del include_document_chunks
        detail_level = InventoryDetailLevel(detail)
        return MemoryInventoryReport.model_validate(
            {
                "detail": detail_level,
                "summary": {
                    "total_records": 2,
                    "scope_counts": {"global": 0, "scoped": 2},
                    "status_counts": {"active": 2},
                    "type_counts": {"decision": 1, "project_fact": 1},
                },
                "scopes": {
                    "distinct_scope_tuples": 1,
                    "global_records": 0,
                    "scoped_records": 2,
                    "user_ids": {"count": 0, "names": [], "truncated": False, "remaining": 0},
                    "project_ids": {
                        "count": 1,
                        "names": ["arcade"][:names_limit] if include_names else [],
                        "truncated": False,
                        "remaining": 0,
                    },
                    "agent_ids": {"count": 0, "names": [], "truncated": False, "remaining": 0},
                }
                if detail_level == InventoryDetailLevel.FULL
                else None,
                "memory_types": [] if detail_level != InventoryDetailLevel.FULL else [
                    {
                        "memory_type": "decision",
                        "display_name": "Decision",
                        "count": 1,
                        "status_counts": {"active": 1},
                        "scope_counts": {"global": 0, "scoped": 1},
                    },
                    {
                        "memory_type": "project_fact",
                        "display_name": "Project Fact",
                        "count": 1,
                        "status_counts": {"active": 1},
                        "scope_counts": {"global": 0, "scoped": 1},
                    },
                ],
            }
        )

    def close(self) -> None:
        self.closed = True


class FakeMarkdownExampleStore:
    def __init__(self) -> None:
        self.closed = False

    def ingest_document(self, path: Path, _scope: Scope) -> Any:
        from memory_store.models import IngestResult

        return IngestResult(path=path, added=3, updated=1, removed=0, unchanged=2)

    def search_document_chunks(
        self, *, text: str, scope: Scope, limit: int = 10
    ) -> list[ChunkSearchResult]:
        return [
            ChunkSearchResult(
                memory_id="chunk-1",
                chunk_id="docs/architecture.md#hybrid-retrieval:0",
                source_path="docs/architecture.md",
                source_hash="hash-chunk-1",
                title="Architecture",
                summary="Chunk retrieval example",
                text="Hybrid retrieval chunk body.",
                tags=["docs"],
                heading_path=["Architecture", "Retrieval"],
                section_index=1,
                section_chunk_index=0,
                document_chunk_index=1,
                final_score=0.76,
                component_scores={"full_text": 0.76},
                normalized_scores={"full_text": 0.76},
                metadata={"approximate_token_count": 9},
            )
        ]

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        return ChunkContextResponse(
            chunk=ChunkSearchResult(
                memory_id="chunk-1",
                chunk_id=chunk_id,
                source_path="docs/architecture.md",
                source_hash="hash-chunk-1",
                title="Architecture",
                summary="Chunk retrieval example",
                text="Hybrid retrieval chunk body.",
                tags=["docs"],
                heading_path=["Architecture", "Retrieval"],
                section_index=1,
                section_chunk_index=0,
                document_chunk_index=1,
                metadata={"approximate_token_count": 9},
            ),
            before=[
                ChunkSearchResult(
                    memory_id="chunk-0",
                    chunk_id="docs/architecture.md#overview:0",
                    source_path="docs/architecture.md",
                    source_hash="hash-chunk-0",
                    title="Architecture",
                    summary="Chunk retrieval example",
                    text="Overview chunk body.",
                    tags=["docs"],
                    heading_path=["Architecture", "Overview"],
                    section_index=0,
                    section_chunk_index=0,
                    document_chunk_index=0,
                )
            ][: before or None],
            after=[
                ChunkSearchResult(
                    memory_id="chunk-2",
                    chunk_id="docs/architecture.md#scoring:0",
                    source_path="docs/architecture.md",
                    source_hash="hash-chunk-2",
                    title="Architecture",
                    summary="Chunk retrieval example",
                    text="Scoring chunk body.",
                    tags=["docs"],
                    heading_path=["Architecture", "Scoring"],
                    section_index=2,
                    section_chunk_index=0,
                    document_chunk_index=2,
                )
            ][: after or None],
        )

    def search(self, _query: Any) -> list[MemorySearchResult]:
        return [
            _result(
                _record(
                    "chunk-1",
                    memory_type=MemoryType.DOCUMENT_CHUNK,
                    chunk_id="docs/architecture.md#hybrid-retrieval:0",
                ),
                final_score=0.76,
                component_scores={"full_text": 0.76},
                normalized_scores={"full_text": 0.76},
            )
        ]

    def close(self) -> None:
        self.closed = True


def test_eval_metrics_helpers_cover_phase_11_requirements() -> None:
    assert recall_at_k([True, False, True], total_relevant=2, k=10) == 1.0
    assert reciprocal_rank([0.0, 2.0, 0.0], k=10) == 0.5
    assert round(ndcg_at_k([3.0, 0.0, 1.0], ideal_relevances=[3.0, 1.0], k=10), 4) == 0.9828
    assert percentile([10.0, 20.0, 30.0], 95.0) == 29.0
    assert duplicate_rate(["a", "a", "b"]) == 1 / 3
    assert stale_memory_rate(["active", "superseded", "expired"]) == 2 / 3

    summary = aggregate_metrics(
        [],
        passed_count=0,
    )
    assert summary.queries == 0
    assert summary.passed == 0


def test_eval_runner_loads_fixture_and_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    fixture_path = tmp_path / "golden_queries.yaml"
    fixture_path.write_text(
        """
queries:
  - id: rerank-decision
    query: What reranking approach did we choose?
    expected_memory_ids:
      - decision_fastembed_reranker
  - id: bb1-architecture
    query: What is the BB1 architecture?
    expected_memory_types:
      - document_chunk
""".strip(),
        encoding="utf-8",
    )

    fixture = load_golden_queries(fixture_path)
    assert [query.id for query in fixture.queries] == ["rerank-decision", "bb1-architecture"]

    output_dir = tmp_path / "reports"
    store = FakeEvalStore()
    summary = run(fixture_path, output_dir=output_dir, store=store)

    assert store.closed is False
    assert store.queries == [
        "What reranking approach did we choose?",
        "What is the BB1 architecture?",
    ]
    assert summary.metrics.queries == 2
    assert summary.metrics.passed == 2
    assert summary.json_report_path.exists()
    assert summary.markdown_report_path.exists()

    payload = json.loads(summary.json_report_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["queries"] == 2
    assert payload["metrics"]["passed"] == 2
    assert payload["queries"][0]["matched_memory_ids"] == ["decision_fastembed_reranker"]
    assert payload["queries"][1]["metrics"]["duplicate_rate"] > 0.0
    assert payload["queries"][1]["metrics"]["stale_memory_rate"] > 0.0
    assert payload["queries"][0]["results"][0]["component_scores"]["reranker"] == 0.95

    markdown = summary.markdown_report_path.read_text(encoding="utf-8")
    assert "# Eval Report" in markdown
    assert "Mean Recall@10" in markdown
    assert "decision_fastembed_reranker" in markdown
    assert "Normalized Scores" in markdown


def test_eval_runner_can_assert_chunk_ids_and_context_windows(tmp_path: Path) -> None:
    fixture_path = tmp_path / "chunk_queries.yaml"
    fixture_path.write_text(
        "queries:\n"
        "  - id: deployment-window\n"
        "    mode: chunk\n"
        "    query: Show the deployment chunk.\n"
        "    scope:\n"
        "      project_id: arcade\n"
        "    before: 1\n"
        "    after: 1\n"
        "    expected_memory_types:\n"
        "      - document_chunk\n"
        "    expected_chunk_ids:\n"
        "      - chunk-deployment-1\n"
        "    expected_before_chunk_ids:\n"
        "      - chunk-overview-0\n"
        "    expected_after_chunk_ids:\n"
        "      - chunk-rollout-2\n",
        encoding="utf-8",
    )

    store = FakeEvalStore()
    summary = run(fixture_path, output_dir=tmp_path / "reports", store=store)

    assert summary.metrics.queries == 1
    assert summary.metrics.passed == 1
    assert summary.queries[0].matched_chunk_ids == ["chunk-deployment-1"]
    assert summary.queries[0].matched_before_chunk_ids == ["chunk-overview-0"]
    assert summary.queries[0].matched_after_chunk_ids == ["chunk-rollout-2"]
    assert summary.queries[0].results[0]["chunk_id"] == "chunk-deployment-1"
    assert summary.queries[0].results[0]["debug"]["context_before_chunk_ids"] == [
        "chunk-overview-0"
    ]


@pytest.mark.skipif(
    not Path("docs/architecture.md").exists(),
    reason="workspace docs fixture is required",
)
def test_eval_runner_matches_expected_memory_with_real_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_module, "FastEmbedProvider", _FakeProvider)

    fixture_path = tmp_path / "golden_queries.yaml"
    store = MemoryStore.from_config(
        database={"path": tmp_path / "arcade", "schema_version": 1},
        embeddings={"dim": 4, "model": "stub-model"},
        reranker={"enabled": False},
        retrieval={"graph_expansion_enabled": False},
    )

    try:
        scope = Scope(project_id="arcade")
        hybrid = store.add_memory(
            memory=MemoryRecord(
                memory_id="seed-hybrid",
                scope=scope,
                stable_key="decision_fastembed_reranker",
                memory_type=MemoryType.DECISION,
                title="Reranker decision",
                summary="Hybrid search should find this record.",
                text="Alpha reranking decision for hybrid search.",
                embedding=[1.0, 0.0, 0.0, 0.0],
                embedding_dim=4,
            )
        )
        store.add_memory(
            MemoryRecord(
                memory_id="seed-distractor",
                scope=scope,
                stable_key="decision_other",
                memory_type=MemoryType.DECISION,
                title="Other decision",
                summary="Distractor",
                text="Beta deployment note for a different topic.",
                embedding=[0.0, 1.0, 0.0, 0.0],
                embedding_dim=4,
            )
        )

        fixture_path.write_text(
            f"""
queries:
  - id: real-hybrid
    query: What reranking approach did we choose?
    scope:
      project_id: arcade
    expected_memory_ids:
      - {hybrid.memory_id}
    expected_memory_types:
      - decision
""".strip(),
            encoding="utf-8",
        )

        summary = run(fixture_path, output_dir=tmp_path / "reports", store=store)
        assert summary.metrics.queries == 1
        assert summary.metrics.passed == 1
        assert summary.queries[0].matched_memory_ids == [hybrid.memory_id]
    finally:
        store.close()


def test_readme_contains_phase_11_sections() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "## What It Does" in readme
    assert "## Quickstart With The Python API" in readme
    assert "## Markdown Ingestion Example" in readme
    assert "## Hybrid Retrieval Example" in readme
    assert "## REST API Example" in readme
    assert "## CLI Examples" in readme
    assert "## Configuration Reference" in readme
    assert "## Lifecycle Examples" in readme
    assert "## Privacy, Delete, Export, And Import Examples" in readme
    assert "## Eval Runner Example" in readme
    assert "## Known V1 Limitations" in readme


def test_examples_are_smoke_tested_where_practical(capsys) -> None:
    python_example = runpy.run_path(str(Path("examples/python_api_example.py")))
    python_example["run_example"](store_factory=lambda **_kwargs: FakePythonExampleStore())
    python_output = capsys.readouterr().out
    assert "created=" in python_output
    assert "top_result=" in python_output
    assert "inventory_project=arcade" in python_output

    markdown_example = runpy.run_path(str(Path("examples/markdown_ingest_example.py")))
    markdown_example["run_example"](store_factory=lambda **_kwargs: FakeMarkdownExampleStore())
    markdown_output = capsys.readouterr().out
    assert "added=3 updated=1 removed=0 unchanged=2" in markdown_output
    assert "top_chunk=" in markdown_output
    assert "context_before=1 context_after=1" in markdown_output

    rest_example = runpy.run_path(str(Path("examples/rest_example.py")))
    rest_example["main"]()
    rest_output = capsys.readouterr().out
    assert "demo-ready" in rest_output
    assert "rest-demo-chunk-1" in rest_output
    assert "rest-demo-chunk-2" in rest_output

    eval_example = runpy.run_path(str(Path("examples/eval_runner_example.py")))
    eval_example["main"]()
    eval_output = capsys.readouterr().out
    assert "Eval complete for" in eval_output
    assert "passed" in eval_output
