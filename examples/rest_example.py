"""In-process REST example using a small injected store stub."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bytebox.api import create_inprocess_app
from bytebox.models import (
    HealthStatus,
    InventoryDetailLevel,
    MemoryInventoryReport,
    MemoryRecord,
    MemorySearchResult,
    MemoryStats,
    Scope,
)


def _chunk_payload(
    *,
    chunk_id: str,
    memory_id: str,
    text: str,
    document_chunk_index: int,
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "chunk_id": chunk_id,
        "source_path": "docs/architecture.md",
        "source_hash": f"hash-{chunk_id}",
        "title": "Architecture",
        "summary": "Chunk-first REST demo.",
        "text": text,
        "tags": ["docs", "demo"],
        "heading_path": ["Architecture", "Deployment"],
        "section_index": document_chunk_index,
        "section_chunk_index": 0,
        "document_chunk_index": document_chunk_index,
        "final_score": 0.82,
        "component_scores": {"retrieval_fusion": 0.82},
        "normalized_scores": {"retrieval_fusion": 0.82},
        "debug": {"route": "rest-example"},
        "metadata": {"approximate_token_count": 12, "chunking_max_tokens_is_approximate": True},
    }


def _record() -> MemoryRecord:
    now = datetime.now(timezone.utc)
    return MemoryRecord(
        memory_id="rest-demo-1",
        scope=Scope(project_id="arcade"),
        stable_key="demo:rest",
        title="REST demo",
        summary="Thin REST route example.",
        text="The REST adapter delegates to the shared service layer.",
        created_at=now,
        updated_at=now,
    )


class ExampleStore:
    def __init__(self) -> None:
        self.record = _record()

    def close(self) -> None:
        return None

    def search(self, _query: Any) -> list[MemorySearchResult]:
        return [
            MemorySearchResult(
                memory=self.record,
                final_score=0.87,
                component_scores={"full_text": 0.87},
                normalized_scores={"full_text": 0.87},
                debug={"route": "rest-example"},
            )
        ]

    def search_document_chunks(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            _chunk_payload(
                chunk_id="rest-demo-chunk-1",
                memory_id="rest-demo-memory-1",
                text="Deployment chunk from the REST demo.",
                document_chunk_index=1,
            )
        ]

    def get_chunk_context(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "chunk": _chunk_payload(
                chunk_id="rest-demo-chunk-1",
                memory_id="rest-demo-memory-1",
                text="Deployment chunk from the REST demo.",
                document_chunk_index=1,
            ),
            "before": [
                _chunk_payload(
                    chunk_id="rest-demo-chunk-0",
                    memory_id="rest-demo-memory-0",
                    text="Overview chunk from the REST demo.",
                    document_chunk_index=0,
                )
            ],
            "after": [
                _chunk_payload(
                    chunk_id="rest-demo-chunk-2",
                    memory_id="rest-demo-memory-2",
                    text="Rollout chunk from the REST demo.",
                    document_chunk_index=2,
                )
            ],
        }

    def health(self) -> HealthStatus:
        return HealthStatus(
            status="ok",
            database_path=Path("./demo-db"),
            schema_version=1,
            dependencies={"arcadedb_embedded": True, "fastembed": True},
            message="demo-ready",
        )

    def stats(self) -> MemoryStats:
        return MemoryStats(
            total_records=1,
            scope_counts={"global": 0, "scoped": 1},
            status_counts={"active": 1},
            type_counts={"observation": 1},
        )

    def inventory(
        self,
        *,
        detail: InventoryDetailLevel | str = InventoryDetailLevel.SUMMARY,
        include_names: bool = False,
        names_limit: int = 100,
        include_document_chunks: bool = True,
    ) -> MemoryInventoryReport:
        del names_limit, include_document_chunks
        detail_level = InventoryDetailLevel(detail)
        return MemoryInventoryReport.model_validate(
            {
                "detail": detail_level,
                "summary": {
                    "total_records": 1,
                    "scope_counts": {"global": 0, "scoped": 1},
                    "status_counts": {"active": 1},
                    "type_counts": {"observation": 1},
                },
                "scopes": {
                    "distinct_scope_tuples": 1,
                    "global_records": 0,
                    "scoped_records": 1,
                    "user_ids": {"count": 0, "names": [], "truncated": False, "remaining": 0},
                    "project_ids": {
                        "count": 1,
                        "names": ["arcade"] if include_names else [],
                        "truncated": False,
                        "remaining": 0,
                    },
                    "agent_ids": {"count": 0, "names": [], "truncated": False, "remaining": 0},
                }
                if detail_level == InventoryDetailLevel.FULL
                else None,
                "memory_types": [
                    {
                        "memory_type": "observation",
                        "display_name": "Observation",
                        "count": 1,
                        "status_counts": {"active": 1},
                        "scope_counts": {"global": 0, "scoped": 1},
                        "oldest_created_at": self.record.created_at,
                        "newest_updated_at": self.record.updated_at,
                    }
                ]
                if detail_level == InventoryDetailLevel.FULL
                else [],
            }
        )


def main() -> None:
    app = create_inprocess_app(store=ExampleStore())

    with TestClient(app) as client:
        print(client.get("/health").json()["message"])
        print(client.get("/stats").json()["total_records"])
        print(client.get("/inventory", params={"detail": "full", "include_names": True}).json()["scopes"]["project_ids"]["names"][0])
        top_chunk = client.post(
            "/chunks/search",
            json={"scope": {"project_id": "arcade"}, "text": "deployment architecture"},
        ).json()[0]
        print(top_chunk["chunk_id"])
        print(
            client.get(
                f"/chunks/{top_chunk['chunk_id']}/context",
                params={"project_id": "arcade", "before": 1, "after": 1},
            ).json()["after"][0]["chunk_id"]
        )


if __name__ == "__main__":
    main()
