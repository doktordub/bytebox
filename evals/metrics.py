"""Evaluation metrics for golden-query retrieval checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

_STALE_STATUSES = frozenset(
    {
        "superseded",
        "contradicted",
        "expired",
        "deleted",
        "removed",
        "forgotten",
    }
)


@dataclass(slots=True)
class QueryMetrics:
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    latency_ms: float = 0.0
    reranker_time_ms: float = 0.0
    reranker_input_size: int = 0
    duplicate_rate: float = 0.0
    stale_memory_rate: float = 0.0


@dataclass(slots=True)
class EvalMetrics:
    queries: int = 0
    passed: int = 0
    mean_recall_at_10: float = 0.0
    mean_mrr: float = 0.0
    mean_ndcg: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    reranker_time_total_ms: float = 0.0
    reranker_time_p50_ms: float = 0.0
    reranker_time_p95_ms: float = 0.0
    reranker_input_documents_total: int = 0
    duplicate_rate: float = 0.0
    stale_memory_rate: float = 0.0


def recall_at_k(relevant_hits: Sequence[bool], *, total_relevant: int, k: int = 10) -> float:
    if total_relevant <= 0:
        return 1.0
    if k <= 0:
        return 0.0

    hits = sum(1 for matched in relevant_hits[:k] if matched)
    return min(hits / total_relevant, 1.0)


def reciprocal_rank(relevances: Sequence[float], *, k: int = 10) -> float:
    if k <= 0:
        return 0.0

    for index, relevance in enumerate(relevances[:k], start=1):
        if relevance > 0.0:
            return 1.0 / index
    return 0.0


def ndcg_at_k(
    relevances: Sequence[float],
    *,
    ideal_relevances: Sequence[float] | None = None,
    k: int = 10,
) -> float:
    if k <= 0:
        return 0.0

    actual = _dcg(relevances[:k])
    if ideal_relevances is None:
        ideal = _dcg(sorted(relevances, reverse=True)[:k])
    else:
        ideal = _dcg(sorted(ideal_relevances, reverse=True)[:k])
    if ideal == 0.0:
        return 0.0
    return actual / ideal


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (max(0.0, min(pct, 100.0)) / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def duplicate_rate(keys: Sequence[str]) -> float:
    if not keys:
        return 0.0

    duplicate_count = len(keys) - len(set(keys))
    return duplicate_count / len(keys)


def stale_memory_rate(statuses: Sequence[str]) -> float:
    if not statuses:
        return 0.0

    stale = sum(1 for status in statuses if status in _STALE_STATUSES)
    return stale / len(statuses)


def aggregate_metrics(query_metrics: Sequence[QueryMetrics], *, passed_count: int) -> EvalMetrics:
    if not query_metrics:
        return EvalMetrics(queries=0, passed=0)

    recall_values = [metric.recall_at_10 for metric in query_metrics]
    mrr_values = [metric.mrr for metric in query_metrics]
    ndcg_values = [metric.ndcg for metric in query_metrics]
    latency_values = [metric.latency_ms for metric in query_metrics]
    reranker_values = [metric.reranker_time_ms for metric in query_metrics]
    duplicate_values = [metric.duplicate_rate for metric in query_metrics]
    stale_values = [metric.stale_memory_rate for metric in query_metrics]

    return EvalMetrics(
        queries=len(query_metrics),
        passed=passed_count,
        mean_recall_at_10=_mean(recall_values),
        mean_mrr=_mean(mrr_values),
        mean_ndcg=_mean(ndcg_values),
        latency_p50_ms=percentile(latency_values, 50.0),
        latency_p95_ms=percentile(latency_values, 95.0),
        reranker_time_total_ms=sum(reranker_values),
        reranker_time_p50_ms=percentile(reranker_values, 50.0),
        reranker_time_p95_ms=percentile(reranker_values, 95.0),
        reranker_input_documents_total=sum(metric.reranker_input_size for metric in query_metrics),
        duplicate_rate=_mean(duplicate_values),
        stale_memory_rate=_mean(stale_values),
    )


def _dcg(relevances: Sequence[float]) -> float:
    score = 0.0
    for index, relevance in enumerate(relevances, start=1):
        if relevance <= 0.0:
            continue
        score += (2.0**float(relevance) - 1.0) / _log2(index + 1)
    return score


def _log2(value: int) -> float:
    from math import log2

    return log2(value)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
