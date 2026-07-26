"""Markdown ingestion example using the shared service layer."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from memory_store import MemoryStore, Scope


class StoreFactory(Protocol):
    def __call__(self, config_path: str | Path | None = None, **overrides: Any) -> Any: ...


def run_example(store_factory: StoreFactory = MemoryStore.from_config) -> None:
    with TemporaryDirectory(prefix="memory-store-markdown-") as temp_dir:
        store = store_factory(
            database={"path": Path(temp_dir) / "arcade", "schema_version": 1},
            reranker={"enabled": False},
        )

        try:
            scope = Scope(project_id="docs")
            result = store.ingest_document(Path("docs/architecture.md"), scope)
            matches = store.search_document_chunks(
                text="hybrid retrieval",
                scope=scope,
                limit=3,
            )

            print(
                "added={added} updated={updated} removed={removed} unchanged={unchanged}".format(
                    added=result.added,
                    updated=result.updated,
                    removed=result.removed,
                    unchanged=result.unchanged,
                )
            )
            print(f"search_results={len(matches)}")
            if matches:
                top_chunk = matches[0]
                context = store.get_chunk_context(
                    top_chunk.chunk_id, scope=scope, before=1, after=1
                )
                print(f"top_chunk={top_chunk.chunk_id}")
                print(
                    f"top_chunk_tokens={top_chunk.metadata.get('approximate_token_count', 'n/a')}"
                )
                print(f"context_before={len(context.before)} context_after={len(context.after)}")
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()


def main() -> None:
    run_example()


if __name__ == "__main__":
    main()
