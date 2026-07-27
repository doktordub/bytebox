"""CLI utility to ingest Markdown files from a folder into the local ByteBox store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bytebox import ByteBox, Scope
from bytebox.models import FolderIngestConnectionStrategy

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = REPO_ROOT / "data" / "bytebox"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest all Markdown files under a folder into the local ByteBox store."
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder to scan recursively for .md files.",
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"ArcadeDB path to write to. Default: {DEFAULT_DATABASE_PATH}",
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
    parser.add_argument(
        "--reranker-enabled",
        action="store_true",
        help="Enable reranking instead of the default disabled mode.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed document instead of continuing.",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Resume from a relative Markdown path inside the target folder.",
    )
    parser.add_argument(
        "--connection-strategy",
        choices=[strategy.value for strategy in FolderIngestConnectionStrategy],
        default=FolderIngestConnectionStrategy.REOPEN_ON_FAILURE.value,
        help="Choose whether to share one store, reopen per file, or reopen after failures.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional JSON report file written after the run.",
    )
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


def main() -> int:
    args = parse_args()
    folder = args.folder.expanduser().resolve()

    if not folder.is_dir():
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": f"Folder does not exist: {folder}",
                },
                indent=2,
            )
        )
        return 2

    scope = build_scope(args)

    store = build_store(args)
    try:
        result = store.ingest_folder(
            folder,
            scope,
            stop_on_error=args.stop_on_error,
            resume_from=args.resume_from,
            connection_strategy=args.connection_strategy,
        )
    finally:
        close_store(store)

    payload: dict[str, Any] = {
        **result.model_dump(mode="json"),
        "message": f"Processed {result.files_processed} of {result.matched_files} Markdown file(s).",
        "folder": str(folder),
        "database_path": str(args.database_path.expanduser().resolve()),
        "scope": {
            "user_id": args.user_id,
            "project_id": args.project_id,
            "agent_id": args.agent_id,
        },
    }
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nResult: ")
    print(json.dumps(payload, indent=2))
    return 0 if result.failed_files == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())