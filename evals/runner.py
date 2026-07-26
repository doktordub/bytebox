"""Golden-query evaluation runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field, model_validator

from memory_store import MemorySearchQuery, MemoryStore, Scope
from memory_store.models import (
    ChunkSearchResult,
    MemoryModel,
    MemorySearchResult,
    MemoryStatus,
    MemoryType,
)

from .metrics import (
    EvalMetrics,
    QueryMetrics,
    aggregate_metrics,
    duplicate_rate,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    stale_memory_rate,
)
from .report import build_json_summary, render_markdown_report

_ID_GAIN = 3.0
_TYPE_GAIN = 1.0
_STATUS_GAIN = 1.0


class GoldenQuery(MemoryModel):
    id: str | None = None
    text: str = Field(min_length=1, validation_alias=AliasChoices("text", "query"))
    scope: Scope = Field(default_factory=Scope)
    limit: int = Field(default=10, ge=1)
    mode: Literal["memory", "chunk"] = "memory"
    before: int = Field(default=0, ge=0)
    after: int = Field(default=0, ge=0)
    expected_memory_ids: list[str] = Field(default_factory=list)
    expected_memory_types: list[MemoryType] = Field(default_factory=list)
    expected_statuses: list[MemoryStatus] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_before_chunk_ids: list[str] = Field(default_factory=list)
    expected_after_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def assign_default_id(self) -> "GoldenQuery":
        if self.id is None:
            self.id = _slugify(self.text)
        return self


class GoldenQueryFixture(MemoryModel):
    queries: list[GoldenQuery]


@dataclass(slots=True)
class EvaluatedQuery:
    id: str
    text: str
    scope: Scope
    limit: int
    mode: str
    passed: bool
    latency_ms: float
    returned_count: int
    expected_memory_ids: list[str]
    expected_memory_types: list[str]
    expected_statuses: list[str]
    expected_chunk_ids: list[str]
    expected_before_chunk_ids: list[str]
    expected_after_chunk_ids: list[str]
    matched_memory_ids: list[str]
    matched_memory_types: list[str]
    matched_statuses: list[str]
    matched_chunk_ids: list[str]
    matched_before_chunk_ids: list[str]
    matched_after_chunk_ids: list[str]
    metrics: QueryMetrics
    results: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "scope": self.scope.model_dump(mode="json"),
            "limit": self.limit,
            "mode": self.mode,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "returned_count": self.returned_count,
            "expected_memory_ids": self.expected_memory_ids,
            "expected_memory_types": self.expected_memory_types,
            "expected_statuses": self.expected_statuses,
            "expected_chunk_ids": self.expected_chunk_ids,
            "expected_before_chunk_ids": self.expected_before_chunk_ids,
            "expected_after_chunk_ids": self.expected_after_chunk_ids,
            "matched_memory_ids": self.matched_memory_ids,
            "matched_memory_types": self.matched_memory_types,
            "matched_statuses": self.matched_statuses,
            "matched_chunk_ids": self.matched_chunk_ids,
            "matched_before_chunk_ids": self.matched_before_chunk_ids,
            "matched_after_chunk_ids": self.matched_after_chunk_ids,
            "metrics": {
                "recall_at_10": self.metrics.recall_at_10,
                "mrr": self.metrics.mrr,
                "ndcg": self.metrics.ndcg,
                "latency_ms": self.metrics.latency_ms,
                "reranker_time_ms": self.metrics.reranker_time_ms,
                "reranker_input_size": self.metrics.reranker_input_size,
                "duplicate_rate": self.metrics.duplicate_rate,
                "stale_memory_rate": self.metrics.stale_memory_rate,
            },
            "results": self.results,
        }


@dataclass(slots=True)
class EvalRunSummary:
    golden_queries_path: Path
    json_report_path: Path
    markdown_report_path: Path
    generated_at: str
    metrics: EvalMetrics
    queries: list[EvaluatedQuery]

    def to_dict(self) -> dict[str, Any]:
        return build_json_summary(
            golden_queries_path=self.golden_queries_path,
            json_report_path=self.json_report_path,
            markdown_report_path=self.markdown_report_path,
            generated_at=self.generated_at,
            metrics=self.metrics,
            queries=[query.to_dict() for query in self.queries],
        )

    def __str__(self) -> str:
        normalized_fixture = str(self.golden_queries_path).replace("\\", "/")
        normalized_json = str(self.json_report_path).replace("\\", "/")
        normalized_markdown = str(self.markdown_report_path).replace("\\", "/")
        return (
            f"Eval complete for {normalized_fixture} -> "
            f"JSON {normalized_json}, Markdown {normalized_markdown}"
        )


def load_golden_queries(path: Path | str) -> GoldenQueryFixture:
    fixture_path = Path(path)
    payload = yaml.safe_load(fixture_path.read_text(encoding="utf-8")) or {}
    return GoldenQueryFixture.model_validate(payload)


def run(
    golden_queries_path: Path | None = None,
    *,
    output_dir: Path | None = None,
    store: Any | None = None,
    config_path: Path | str | None = None,
) -> EvalRunSummary:
    fixture_path = Path(golden_queries_path or Path("evals/golden_queries.yaml"))
    fixture = load_golden_queries(fixture_path)
    destination = output_dir or fixture_path.parent
    destination.mkdir(parents=True, exist_ok=True)

    json_report_path = destination / f"{fixture_path.stem}.summary.json"
    markdown_report_path = destination / f"{fixture_path.stem}.report.md"

    created_store = False
    active_store = store
    if active_store is None:
        active_store = MemoryStore.from_config(config_path)
        created_store = True

    try:
        evaluated_queries = [_evaluate_query(active_store, query) for query in fixture.queries]
    finally:
        if created_store:
            close = getattr(active_store, "close", None)
            if callable(close):
                close()

    passed_count = sum(1 for query in evaluated_queries if query.passed)
    metrics = aggregate_metrics(
        [query.metrics for query in evaluated_queries],
        passed_count=passed_count,
    )
    summary = EvalRunSummary(
        golden_queries_path=fixture_path,
        json_report_path=json_report_path,
        markdown_report_path=markdown_report_path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        metrics=metrics,
        queries=evaluated_queries,
    )
    payload = summary.to_dict()
    json_report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_report_path.write_text(render_markdown_report(payload), encoding="utf-8")
    return summary


def main() -> None:
    print(run())


def _evaluate_query(store: Any, spec: GoldenQuery) -> EvaluatedQuery:
    if spec.mode == "chunk":
        return _evaluate_chunk_query(store, spec)

    return _evaluate_memory_query(store, spec)


def _evaluate_memory_query(store: Any, spec: GoldenQuery) -> EvaluatedQuery:
    started = perf_counter()
    results = store.search(
        MemorySearchQuery(
            scope=spec.scope,
            text=spec.text,
            limit=spec.limit,
        )
    )
    latency_ms = (perf_counter() - started) * 1000.0

    serialized_results = [
        _serialize_result(rank=index, result=result)
        for index, result in enumerate(results, start=1)
    ]

    expected_type_values = [memory_type.value for memory_type in spec.expected_memory_types]
    expected_status_values = [status.value for status in spec.expected_statuses]
    matched_memory_ids = sorted(
        {
            result.memory.memory_id
            for result in results
            if result.memory.memory_id in spec.expected_memory_ids
        }
    )
    matched_memory_types = sorted(
        {
            result.memory.memory_type.value
            for result in results
            if result.memory.memory_type in spec.expected_memory_types
        }
    )
    matched_statuses = sorted(
        {
            result.memory.status.value
            for result in results
            if result.memory.status in spec.expected_statuses
        }
    )

    expected_units = (
        len(spec.expected_memory_ids)
        + len(spec.expected_memory_types)
        + len(spec.expected_statuses)
    )
    matched_units = len(matched_memory_ids) + len(matched_memory_types) + len(matched_statuses)
    relevances = [_relevance_score(result, spec) for result in results]
    query_metrics = QueryMetrics(
        recall_at_10=recall_at_k(
            [relevance > 0.0 for relevance in relevances],
            total_relevant=expected_units,
            k=min(spec.limit, 10),
        )
        if expected_units > 0
        else 1.0,
        mrr=reciprocal_rank(relevances, k=min(spec.limit, 10)),
        ndcg=ndcg_at_k(
            relevances,
            ideal_relevances=_ideal_relevances(spec),
            k=min(spec.limit, 10),
        ),
        latency_ms=latency_ms,
        reranker_time_ms=_extract_reranker_time_ms(results),
        reranker_input_size=_extract_reranker_input_size(results),
        duplicate_rate=duplicate_rate([_result_key(result) for result in results]),
        stale_memory_rate=stale_memory_rate([result.memory.status.value for result in results]),
    )

    passed = (
        set(spec.expected_memory_ids).issubset(matched_memory_ids)
        and set(expected_type_values).issubset(matched_memory_types)
        and set(expected_status_values).issubset(matched_statuses)
    )
    if expected_units == 0:
        passed = matched_units == 0 or bool(results)

    return EvaluatedQuery(
        id=spec.id or _slugify(spec.text),
        text=spec.text,
        scope=spec.scope,
        limit=spec.limit,
        mode=spec.mode,
        passed=passed,
        latency_ms=latency_ms,
        returned_count=len(results),
        expected_memory_ids=list(spec.expected_memory_ids),
        expected_memory_types=expected_type_values,
        expected_statuses=expected_status_values,
        expected_chunk_ids=list(spec.expected_chunk_ids),
        expected_before_chunk_ids=list(spec.expected_before_chunk_ids),
        expected_after_chunk_ids=list(spec.expected_after_chunk_ids),
        matched_memory_ids=matched_memory_ids,
        matched_memory_types=matched_memory_types,
        matched_statuses=matched_statuses,
        matched_chunk_ids=[],
        matched_before_chunk_ids=[],
        matched_after_chunk_ids=[],
        metrics=query_metrics,
        results=serialized_results,
    )


def _evaluate_chunk_query(store: Any, spec: GoldenQuery) -> EvaluatedQuery:
    started = perf_counter()
    results = store.search_document_chunks(
        text=spec.text,
        scope=spec.scope,
        limit=spec.limit,
    )
    latency_ms = (perf_counter() - started) * 1000.0

    serialized_results = [
        _serialize_chunk_result(rank=index, result=result)
        for index, result in enumerate(results, start=1)
    ]

    matched_chunk_ids = sorted(
        {result.chunk_id for result in results if result.chunk_id in spec.expected_chunk_ids}
    )

    matched_memory_types: list[str] = []
    if results and MemoryType.DOCUMENT_CHUNK in spec.expected_memory_types:
        matched_memory_types = [MemoryType.DOCUMENT_CHUNK.value]

    context_before_ids: list[str] = []
    context_after_ids: list[str] = []
    if results and (
        spec.before > 0
        or spec.after > 0
        or spec.expected_before_chunk_ids
        or spec.expected_after_chunk_ids
    ):
        context = store.get_chunk_context(
            results[0].chunk_id,
            scope=spec.scope,
            before=spec.before,
            after=spec.after,
        )
        context_before_ids = [item.chunk_id for item in context.before]
        context_after_ids = [item.chunk_id for item in context.after]
        serialized_results[0]["debug"] = {
            **serialized_results[0]["debug"],
            "context_before_chunk_ids": context_before_ids,
            "context_after_chunk_ids": context_after_ids,
        }

    matched_before_chunk_ids = sorted(
        chunk_id for chunk_id in context_before_ids if chunk_id in spec.expected_before_chunk_ids
    )
    matched_after_chunk_ids = sorted(
        chunk_id for chunk_id in context_after_ids if chunk_id in spec.expected_after_chunk_ids
    )

    expected_type_values = [memory_type.value for memory_type in spec.expected_memory_types]
    expected_units = len(spec.expected_chunk_ids)
    relevances = [_chunk_relevance_score(result, spec) for result in results]
    query_metrics = QueryMetrics(
        recall_at_10=recall_at_k(
            [relevance > 0.0 for relevance in relevances],
            total_relevant=expected_units,
            k=min(spec.limit, 10),
        )
        if expected_units > 0
        else 1.0,
        mrr=reciprocal_rank(relevances, k=min(spec.limit, 10)),
        ndcg=ndcg_at_k(
            relevances,
            ideal_relevances=[_ID_GAIN] * len(spec.expected_chunk_ids),
            k=min(spec.limit, 10),
        ),
        latency_ms=latency_ms,
        reranker_time_ms=_extract_chunk_reranker_time_ms(results),
        reranker_input_size=_extract_chunk_reranker_input_size(results),
        duplicate_rate=duplicate_rate([result.chunk_id for result in results]),
        stale_memory_rate=0.0,
    )

    passed = (
        set(spec.expected_chunk_ids).issubset(matched_chunk_ids)
        and set(spec.expected_before_chunk_ids).issubset(matched_before_chunk_ids)
        and set(spec.expected_after_chunk_ids).issubset(matched_after_chunk_ids)
        and set(expected_type_values).issubset(matched_memory_types)
    )
    if (
        expected_units == 0
        and not spec.expected_before_chunk_ids
        and not spec.expected_after_chunk_ids
        and not expected_type_values
    ):
        passed = bool(results)

    return EvaluatedQuery(
        id=spec.id or _slugify(spec.text),
        text=spec.text,
        scope=spec.scope,
        limit=spec.limit,
        mode=spec.mode,
        passed=passed,
        latency_ms=latency_ms,
        returned_count=len(results),
        expected_memory_ids=list(spec.expected_memory_ids),
        expected_memory_types=expected_type_values,
        expected_statuses=[],
        expected_chunk_ids=list(spec.expected_chunk_ids),
        expected_before_chunk_ids=list(spec.expected_before_chunk_ids),
        expected_after_chunk_ids=list(spec.expected_after_chunk_ids),
        matched_memory_ids=[],
        matched_memory_types=matched_memory_types,
        matched_statuses=[],
        matched_chunk_ids=matched_chunk_ids,
        matched_before_chunk_ids=matched_before_chunk_ids,
        matched_after_chunk_ids=matched_after_chunk_ids,
        metrics=query_metrics,
        results=serialized_results,
    )


def _relevance_score(result: MemorySearchResult, spec: GoldenQuery) -> float:
    relevance = 0.0
    if result.memory.memory_id in spec.expected_memory_ids:
        relevance += _ID_GAIN
    if result.memory.memory_type in spec.expected_memory_types:
        relevance += _TYPE_GAIN
    if result.memory.status in spec.expected_statuses:
        relevance += _STATUS_GAIN
    return relevance


def _ideal_relevances(spec: GoldenQuery) -> list[float]:
    ideal: list[float] = []
    ideal.extend([_ID_GAIN] * len(spec.expected_memory_ids))
    ideal.extend([_TYPE_GAIN] * len(spec.expected_memory_types))
    ideal.extend([_STATUS_GAIN] * len(spec.expected_statuses))
    return ideal


def _serialize_result(rank: int, result: MemorySearchResult) -> dict[str, Any]:
    memory = result.memory
    return {
        "rank": rank,
        "memory_id": memory.memory_id,
        "stable_key": memory.stable_key,
        "chunk_id": memory.chunk_id,
        "title": memory.title,
        "summary": memory.summary,
        "memory_type": memory.memory_type.value,
        "status": memory.status.value,
        "scope": memory.scope.model_dump(mode="json"),
        "final_score": result.final_score,
        "component_scores": dict(result.component_scores),
        "normalized_scores": dict(result.normalized_scores),
        "debug": dict(result.debug),
    }


def _serialize_chunk_result(rank: int, result: ChunkSearchResult) -> dict[str, Any]:
    return {
        "rank": rank,
        "memory_id": result.memory_id,
        "stable_key": result.chunk_id,
        "chunk_id": result.chunk_id,
        "title": result.title,
        "summary": result.summary,
        "memory_type": MemoryType.DOCUMENT_CHUNK.value,
        "status": MemoryStatus.ACTIVE.value,
        "scope": Scope().model_dump(mode="json"),
        "source_path": result.source_path,
        "heading_path": list(result.heading_path or []),
        "final_score": result.final_score,
        "component_scores": dict(result.component_scores),
        "normalized_scores": dict(result.normalized_scores),
        "debug": dict(result.debug),
    }


def _chunk_relevance_score(result: ChunkSearchResult, spec: GoldenQuery) -> float:
    if result.chunk_id in spec.expected_chunk_ids:
        return _ID_GAIN
    return 0.0


def _extract_reranker_time_ms(results: list[MemorySearchResult]) -> float:
    return max(
        (_as_float(result.debug.get("rerank_duration_ms")) for result in results),
        default=0.0,
    )


def _extract_chunk_reranker_time_ms(results: list[ChunkSearchResult]) -> float:
    return max(
        (_as_float(result.debug.get("rerank_duration_ms")) for result in results),
        default=0.0,
    )


def _extract_reranker_input_size(results: list[MemorySearchResult]) -> int:
    return max(
        (_as_int(result.debug.get("rerank_input_size")) for result in results),
        default=0,
    )


def _extract_chunk_reranker_input_size(results: list[ChunkSearchResult]) -> int:
    return max(
        (_as_int(result.debug.get("rerank_input_size")) for result in results),
        default=0,
    )


def _result_key(result: MemorySearchResult) -> str:
    memory = result.memory
    return memory.chunk_id or memory.stable_key or memory.memory_id


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _slugify(text: str) -> str:
    parts = [part for part in text.lower().replace("?", "").split() if part]
    slug = "-".join(parts[:6]).strip("-")
    return slug or "query"


if __name__ == "__main__":
    main()
