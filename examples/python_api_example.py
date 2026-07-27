"""Direct Python API example for the local-first ByteBox package."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from bytebox import ByteBox, Scope
from bytebox.models import (
    MemoryCreate,
    MemoryFeedback,
    MemorySearchQuery,
    MemoryType,
)


class StoreFactory(Protocol):
    def __call__(self, config_path: str | Path | None = None, **overrides: Any) -> Any: ...


def run_example(store_factory: StoreFactory = ByteBox.from_config) -> None:
    with TemporaryDirectory(prefix="bytebox-python-api-") as temp_dir:
        store = store_factory(
            database={"path": Path(temp_dir) / "arcade", "schema_version": 1},
            reranker={"enabled": False},
            retrieval={"graph_expansion_enabled": False, "final_top_k": 5},
        )

        try:
            scope = Scope(project_id="arcade")
            record = store.add_memory(
                MemoryCreate(
                    scope=scope,
                    stable_key="decision_fastembed_reranker",
                    memory_type=MemoryType.DECISION,
                    title="Use FastEmbed reranking",
                    text="We chose FastEmbed cross-encoder reranking for bounded hybrid search.",
                    tags=["phase-11", "retrieval"],
                ),
                embed=False,
            )
            store.add_memory(
                MemoryCreate(
                    scope=scope,
                    memory_type=MemoryType.PROJECT_FACT,
                    title="Adapter rule",
                    text="REST and CLI stay thin and delegate to the service layer.",
                ),
                embed=False,
            )

            results = store.search(
                MemorySearchQuery(
                    scope=scope,
                    text="What reranking approach did we choose?",
                    limit=3,
                )
            )
            updated = store.add_feedback(
                record.memory_id,
                MemoryFeedback(positive=True, confirmed=True, confidence=0.95),
            )

            print(f"created={record.memory_id}")
            print(f"top_result={results[0].memory.memory_id if results else 'none'}")
            print(f"confidence={updated.confidence}")
            print(f"total_records={store.stats().total_records}")
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()


def main() -> None:
    run_example()


if __name__ == "__main__":
    main()
