"""Thin command-line adapter over the shared service layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .arcade import unlock_arcade_database
from .api.schemas import to_jsonable
from .config import load_settings
from .models import FolderIngestConnectionStrategy, MemoryExport, MemorySearchQuery, Scope
from .store import MemoryStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-store")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", parents=[common])
    subparsers.add_parser("init", parents=[common])

    ingest_file = subparsers.add_parser("ingest-file", parents=[common])
    ingest_file.add_argument("path", type=Path)
    ingest_file.add_argument("--dry-run", action="store_true")
    _add_scope_arguments(ingest_file)

    ingest_folder = subparsers.add_parser("ingest-folder", parents=[common])
    ingest_folder.add_argument("path", type=Path)
    ingest_folder.add_argument("--dry-run", action="store_true")
    ingest_error_mode = ingest_folder.add_mutually_exclusive_group()
    ingest_error_mode.add_argument(
        "--stop-on-error",
        dest="error_mode",
        action="store_const",
        const="stop",
    )
    ingest_error_mode.add_argument(
        "--continue-on-error",
        dest="error_mode",
        action="store_const",
        const="continue",
    )
    ingest_folder.set_defaults(error_mode=None)
    ingest_folder.add_argument("--resume-from")
    ingest_folder.add_argument(
        "--connection-strategy",
        choices=[strategy.value for strategy in FolderIngestConnectionStrategy],
        default=FolderIngestConnectionStrategy.REOPEN_ON_FAILURE.value,
    )
    ingest_folder.add_argument("--report-path", type=Path, default=None)
    ingest_folder.add_argument("--manifest-path", type=Path, default=None)
    ingest_folder.add_argument("--only-failed", action="store_true")
    ingest_folder.add_argument("--limit", type=int, default=None)
    ingest_folder.add_argument("--since")
    ingest_folder.add_argument("--progress-every-documents", type=int, default=0)
    ingest_folder.add_argument("--progress-every-chunks", type=int, default=0)
    _add_scope_arguments(ingest_folder)

    unlock = subparsers.add_parser("unlock", parents=[common])
    unlock.add_argument("--database-path", type=Path, default=None)
    unlock.add_argument("--force", action="store_true")

    search = subparsers.add_parser("search", parents=[common])
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    _add_scope_arguments(search)

    search_chunks = subparsers.add_parser("search-chunks", parents=[common])
    search_chunks.add_argument("query")
    search_chunks.add_argument("--limit", type=int, default=10)
    _add_scope_arguments(search_chunks)

    chunk_context = subparsers.add_parser("chunk-context", parents=[common])
    chunk_context.add_argument("chunk_id")
    chunk_context.add_argument("--before", type=int, default=1)
    chunk_context.add_argument("--after", type=int, default=1)
    _add_scope_arguments(chunk_context)

    evaluate = subparsers.add_parser("eval", parents=[common])
    evaluate.add_argument("path", nargs="?", type=Path, default=Path("evals/golden_queries.yaml"))

    export = subparsers.add_parser("export", parents=[common])
    export.add_argument("--out", type=Path, required=True)
    _add_scope_arguments(export)

    delete_by_scope = subparsers.add_parser("delete-by-scope", parents=[common])
    delete_by_scope.add_argument("--dry-run", action="store_true")
    delete_by_scope.add_argument("--hard-delete", action="store_true")
    _add_scope_arguments(delete_by_scope)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "eval":
        try:
            result = run_evaluation(args.path, config_path=args.config)
        except ModuleNotFoundError as exc:
            if exc.name == "evals" or exc.name.startswith("evals."):
                parser.error(
                    "The eval command requires the repository eval modules. "
                    "Install from source with `pip install -e .[dev]` or run from the project root."
                )
            raise
        print(str(result).replace("\\", "/"))
        return 0

    if args.command == "unlock":
        settings = load_settings(args.config)
        database_path = args.database_path or settings.database.path
        _print_json(unlock_arcade_database(database_path, force=args.force))
        return 0

    store = MemoryStore.from_config(args.config)

    try:
        if args.command in {"health", "init"}:
            _print_json(store.health())
            return 0

        if args.command == "ingest-file":
            kwargs: dict[str, Any] = {}
            if args.dry_run:
                kwargs["dry_run"] = True
            _print_json(store.ingest_document(args.path, _scope_from_args(args), **kwargs))
            return 0

        if args.command == "ingest-folder":
            kwargs = {
                "stop_on_error": args.error_mode == "stop",
                "resume_from": args.resume_from,
                "connection_strategy": args.connection_strategy,
            }
            if args.error_mode == "continue":
                kwargs["continue_on_error"] = True
            if args.dry_run:
                kwargs["dry_run"] = True
            if args.manifest_path is not None:
                kwargs["manifest_path"] = args.manifest_path
            if args.only_failed:
                kwargs["only_failed"] = True
            if args.limit is not None:
                kwargs["limit"] = args.limit
            if args.since is not None:
                kwargs["since"] = args.since
            if args.progress_every_documents > 0:
                kwargs["progress_every_documents"] = args.progress_every_documents
            if args.progress_every_chunks > 0:
                kwargs["progress_every_chunks"] = args.progress_every_chunks
            if args.progress_every_documents > 0 or args.progress_every_chunks > 0:
                kwargs["progress_callback"] = _print_ingest_progress
            payload = store.ingest_folder(args.path, _scope_from_args(args), **kwargs)
            if args.report_path is not None:
                args.report_path.parent.mkdir(parents=True, exist_ok=True)
                args.report_path.write_text(_to_json(payload), encoding="utf-8")
            _print_json(payload)
            return 0 if payload.failed_files == 0 else 1

        if args.command == "search":
            _print_json(
                store.search(
                    MemorySearchQuery(
                        scope=_scope_from_args(args),
                        text=args.query,
                        limit=args.limit,
                    )
                )
            )
            return 0

        if args.command == "search-chunks":
            _print_json(
                store.search_document_chunks(
                    text=args.query,
                    scope=_scope_from_args(args),
                    limit=args.limit,
                )
            )
            return 0

        if args.command == "chunk-context":
            _print_json(
                store.get_chunk_context(
                    args.chunk_id,
                    scope=_scope_from_args(args),
                    before=args.before,
                    after=args.after,
                )
            )
            return 0

        if args.command == "export":
            payload = _export_payload(store, _scope_from_args(args))
            args.out.write_text(_to_json(payload), encoding="utf-8")
            _print_json(payload)
            return 0

        if args.command == "delete-by-scope":
            scope = _scope_from_args(args)
            if scope.is_global:
                parser.error(
                    "delete-by-scope requires at least one of --user-id, "
                    "--project-id, or --agent-id"
                )

            if args.dry_run:
                preview = store.export_scope(scope)
                _print_json(
                    {
                        "count": len(preview.records),
                        "dry_run": True,
                        "hard_delete": args.hard_delete,
                        "scope": scope,
                    }
                )
                return 0

            _print_json(
                {
                    "deleted": store.delete_by_scope(scope, hard_delete=args.hard_delete),
                    "dry_run": False,
                    "hard_delete": args.hard_delete,
                    "scope": scope,
                }
            )
            return 0
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id")
    parser.add_argument("--project-id")
    parser.add_argument("--agent-id")


def _scope_from_args(args: argparse.Namespace) -> Scope:
    return Scope(
        user_id=getattr(args, "user_id", None),
        project_id=getattr(args, "project_id", None),
        agent_id=getattr(args, "agent_id", None),
    )


def run_evaluation(path: Path, config_path: Path | None = None) -> Any:
    from evals.runner import run

    return run(path, config_path=config_path)


def _export_payload(store: MemoryStore, scope: Scope) -> MemoryExport:
    if scope.user_id is not None and scope.project_id is None and scope.agent_id is None:
        return MemoryExport(scope=scope, records=store.export_user_memories(scope.user_id))
    return store.export_scope(scope)


def _to_json(payload: Any) -> str:
    return json.dumps(to_jsonable(payload), indent=2, sort_keys=True)


def _print_json(payload: Any) -> None:
    print(_to_json(payload))


def _print_ingest_progress(event: dict[str, Any]) -> None:
    kind = event.get("kind")
    if kind == "chunk":
        path = event.get("path", "?")
        completed = event.get("completed_chunks", 0)
        total = event.get("total_chunks", 0)
        phase = event.get("phase", "chunk")
        print(
            f"[progress] {phase} {path} {completed}/{total} chunks",
            file=sys.stderr,
        )
        return

    if kind == "document":
        path = event.get("path", "?")
        processed = event.get("processed_files", 0)
        total = event.get("total_files", 0)
        status = "ok" if event.get("ok", False) else "failed"
        print(
            f"[progress] documents {processed}/{total} {path} {status}",
            file=sys.stderr,
        )
        return

    print(f"[progress] {event}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
