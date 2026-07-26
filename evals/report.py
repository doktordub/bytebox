"""Evaluation report rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .metrics import EvalMetrics


def build_json_summary(
    *,
    golden_queries_path: Path,
    json_report_path: Path,
    markdown_report_path: Path,
    generated_at: str,
    metrics: EvalMetrics,
    queries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "golden_queries_path": _normalize_path(golden_queries_path),
        "json_report_path": _normalize_path(json_report_path),
        "markdown_report_path": _normalize_path(markdown_report_path),
        "metrics": {
            "queries": metrics.queries,
            "passed": metrics.passed,
            "mean_recall_at_10": metrics.mean_recall_at_10,
            "mean_mrr": metrics.mean_mrr,
            "mean_ndcg": metrics.mean_ndcg,
            "latency_p50_ms": metrics.latency_p50_ms,
            "latency_p95_ms": metrics.latency_p95_ms,
            "reranker_time_total_ms": metrics.reranker_time_total_ms,
            "reranker_time_p50_ms": metrics.reranker_time_p50_ms,
            "reranker_time_p95_ms": metrics.reranker_time_p95_ms,
            "reranker_input_documents_total": metrics.reranker_input_documents_total,
            "duplicate_rate": metrics.duplicate_rate,
            "stale_memory_rate": metrics.stale_memory_rate,
        },
        "queries": queries,
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    lines = [
        "# Eval Report",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Fixture: {summary['golden_queries_path']}",
        f"- JSON Summary: {summary['json_report_path']}",
        f"- Markdown Report: {summary['markdown_report_path']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Queries | {metrics['queries']} |",
        f"| Passed | {metrics['passed']} |",
        f"| Mean Recall@10 | {_format_float(metrics['mean_recall_at_10'])} |",
        f"| Mean MRR | {_format_float(metrics['mean_mrr'])} |",
        f"| Mean NDCG | {_format_float(metrics['mean_ndcg'])} |",
        f"| Latency p50 (ms) | {_format_float(metrics['latency_p50_ms'])} |",
        f"| Latency p95 (ms) | {_format_float(metrics['latency_p95_ms'])} |",
        f"| Reranker Time Total (ms) | {_format_float(metrics['reranker_time_total_ms'])} |",
        f"| Reranker Time p50 (ms) | {_format_float(metrics['reranker_time_p50_ms'])} |",
        f"| Reranker Time p95 (ms) | {_format_float(metrics['reranker_time_p95_ms'])} |",
        f"| Reranker Input Documents | {metrics['reranker_input_documents_total']} |",
        f"| Duplicate Rate | {_format_float(metrics['duplicate_rate'])} |",
        f"| Stale Memory Rate | {_format_float(metrics['stale_memory_rate'])} |",
    ]

    for query in summary["queries"]:
        lines.extend(
            [
                "",
                f"## {query['id']}",
                "",
                f"- Query: {query['text']}",
                f"- Passed: {query['passed']}",
                f"- Latency (ms): {_format_float(query['latency_ms'])}",
                f"- Recall@10: {_format_float(query['metrics']['recall_at_10'])}",
                f"- MRR: {_format_float(query['metrics']['mrr'])}",
                f"- NDCG: {_format_float(query['metrics']['ndcg'])}",
                f"- Matched Memory IDs: {', '.join(query['matched_memory_ids']) or '(none)'}",
                f"- Matched Memory Types: {', '.join(query['matched_memory_types']) or '(none)'}",
                f"- Matched Statuses: {', '.join(query['matched_statuses']) or '(none)'}",
                f"- Matched Chunk IDs: {', '.join(query.get('matched_chunk_ids', [])) or '(none)'}",
                "- Matched Context Before IDs: "
                f"{', '.join(query.get('matched_before_chunk_ids', [])) or '(none)'}",
                "- Matched Context After IDs: "
                f"{', '.join(query.get('matched_after_chunk_ids', [])) or '(none)'}",
                "",
                "| Rank | Memory ID | Type | Status | Final Score |",
                " Component Scores | Normalized Scores |",
                "| ---: | --- | --- | --- | ---: | --- | --- |",
            ]
        )

        if not query["results"]:
            lines.append("| - | (no results) | - | - | 0.0000 | {} | {} |")
            continue

        for result in query["results"]:
            row = (
                "| {rank} | {memory_id} | {memory_type} | {status} | {final_score} | "
                "{component_scores} | {normalized_scores} |"
            )
            lines.append(
                row.format(
                    rank=result["rank"],
                    memory_id=result["memory_id"],
                    memory_type=result["memory_type"],
                    status=result["status"],
                    final_score=_format_float(result["final_score"]),
                    component_scores=_json_inline(result["component_scores"]),
                    normalized_scores=_json_inline(result["normalized_scores"]),
                )
            )
            if result.get("chunk_id"):
                lines.append(f"  Chunk ID: {result['chunk_id']}")
            lines.append(f"  Debug: {_json_inline(result['debug'])}")

    return "\n".join(lines) + "\n"


def _format_float(value: float) -> str:
    return f"{float(value):.4f}"


def _json_inline(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")
