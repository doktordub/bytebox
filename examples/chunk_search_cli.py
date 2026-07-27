"""CLI utility to search document chunks from the local ByteBox store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bytebox import ByteBox, Scope

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = REPO_ROOT / "data" / "bytebox"
SNIPPET_LENGTH = 240
TITLE_LENGTH = 72


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the local ByteBox database for document chunks."
    )
    parser.add_argument("query", help="Search text to match against chunk content.")
    parser.add_argument(
        "--database-path",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"ArcadeDB path to read from. Default: {DEFAULT_DATABASE_PATH}",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional config file passed to ByteBox.from_config().",
    )
    parser.add_argument("--user-id", default="", help="Scope user_id. Default: empty.")
    parser.add_argument(
        "--project-id",
        default="default",
        help="Scope project_id. Default: default.",
    )
    parser.add_argument("--agent-id", default="", help="Scope agent_id. Default: empty.")
    parser.add_argument("--limit", type=int, default=10, help="Max results to return. Default: 10.")
    parser.add_argument("--before", type=int, default=0, help="Context chunks to include before a hit. Default: 0.")
    parser.add_argument("--after", type=int, default=0, help="Context chunks to include after a hit. Default: 0.")
    parser.add_argument(
        "--include-removed",
        action="store_true",
        help="Include chunks marked as removed.",
    )
    parser.add_argument(
        "--include-non-retrievable",
        dest="allow_retrieval_only",
        action="store_false",
        help="Do not restrict results to retrievable chunks.",
    )
    parser.add_argument(
        "--reranker-enabled",
        action="store_true",
        help="Enable reranking instead of the default disabled mode.",
    )
    parser.set_defaults(allow_retrieval_only=True)
    return parser.parse_args()


def build_scope(args: argparse.Namespace) -> Scope:
    return Scope(
        user_id=args.user_id,
        project_id=args.project_id,
        agent_id=args.agent_id,
    )


def build_store(args: argparse.Namespace) -> Any:
    return ByteBox.from_config(
        args.config_path,
        database={"path": args.database_path.expanduser(), "schema_version": 1},
        reranker={"enabled": args.reranker_enabled},
    )


def close_store(store: Any) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        close()


def clean_text(value: Any) -> str:
    return str(value or "").replace("\ufeff", "").strip()


def truncate_text(value: Any, limit: int) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def normalize_chunk_result(result: Any) -> dict[str, Any]:
    heading_path = list(getattr(result, "heading_path", None) or [])
    text = clean_text(getattr(result, "text", None))
    summary = clean_text(getattr(result, "summary", None))
    source_path = clean_text(getattr(result, "source_path", None))
    document_chunk_index = getattr(result, "document_chunk_index", None)
    title = (
        clean_text(getattr(result, "title", None))
        or (heading_path[-1] if heading_path else None)
        or truncate_text(text, TITLE_LENGTH)
        or "Untitled chunk"
    )
    score = float(getattr(result, "final_score", 0.0) or 0.0)
    return {
        "memory_id": getattr(result, "memory_id", None),
        "chunk_id": getattr(result, "chunk_id", None),
        "title": title,
        "summary": summary,
        "text": text,
        "snippet": summary or truncate_text(text, SNIPPET_LENGTH),
        "source_path": source_path,
        "source_hash": clean_text(getattr(result, "source_hash", None)),
        "heading_path": heading_path,
        "heading_path_label": " / ".join(heading_path),
        "section_index": getattr(result, "section_index", None),
        "section_chunk_index": getattr(result, "section_chunk_index", None),
        "document_chunk_index": document_chunk_index,
        "tags": list(getattr(result, "tags", None) or []),
        "metadata": dict(getattr(result, "metadata", None) or {}),
        "score": round(score, 4),
        "score_label": f"{score:.3f}",
        "has_context": bool(source_path and document_chunk_index is not None),
    }


def main() -> int:
    args = parse_args()
    scope = build_scope(args)
    store = build_store(args)

    try:
        results = store.search_document_chunks(
            text=args.query,
            scope=scope,
            limit=args.limit,
            before=args.before,
            after=args.after,
            include_removed=args.include_removed,
            allow_retrieval_only=args.allow_retrieval_only,
        )
    finally:
        close_store(store)

    items = [normalize_chunk_result(result) for result in results]
    payload = {
        "ok": True,
        "message": f"Found {len(items)} chunk result(s).",
        "query": args.query,
        "database_path": str(args.database_path.expanduser().resolve()),
        "scope": {
            "user_id": args.user_id,
            "project_id": args.project_id,
            "agent_id": args.agent_id,
        },
        "limit": args.limit,
        "before": args.before,
        "after": args.after,
        "include_removed": args.include_removed,
        "allow_retrieval_only": args.allow_retrieval_only,
        "count": len(items),
        "items": items,
    }
    print("\nSearch results: ")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())