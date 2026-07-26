"""Eval runner example using a deterministic in-memory store stub."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.runner import run
from memory_store.models import MemoryRecord, MemorySearchResult, Scope


def _record() -> MemoryRecord:
    now = datetime.now(timezone.utc)
    return MemoryRecord(
        memory_id="decision_fastembed_reranker",
        scope=Scope(project_id="arcade"),
        stable_key="decision_fastembed_reranker",
        title="FastEmbed reranker",
        summary="Decision record for the reranker choice.",
        text="We chose FastEmbed cross-encoder reranking for hybrid retrieval.",
        created_at=now,
        updated_at=now,
    )


class ExampleEvalStore:
    def search(self, _query: object) -> list[MemorySearchResult]:
        return [
            MemorySearchResult(
                memory=_record(),
                final_score=0.91,
                component_scores={"full_text": 0.9, "retrieval_fusion": 0.8},
                normalized_scores={"full_text": 0.9, "retrieval_fusion": 0.8},
                debug={"rerank_input_size": 1},
            )
        ]


def main() -> None:
    with TemporaryDirectory(prefix="memory-store-eval-") as temp_dir:
        root = Path(temp_dir)
        fixture = root / "golden_queries.yaml"
        fixture.write_text(
            """
queries:
  - id: rerank-decision
    query: What reranking approach did we choose?
    expected_memory_ids:
      - decision_fastembed_reranker
""".strip(),
            encoding="utf-8",
        )

        summary = run(fixture, output_dir=root / "reports", store=ExampleEvalStore())
        print(summary)
        print(summary.to_dict()["metrics"])


if __name__ == "__main__":
    main()