"""Command handlers for the thin ByteBox CLI adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .api.schemas import to_jsonable
from .embeddings import (
    doctor_models,
    export_model_manifest,
    inspect_model,
    install_model,
    list_models,
    verify_models,
)
from .models import MemoryExport, MemorySearchQuery, Scope


def handle_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    store_factory: Callable[[Path | None], Any],
    settings_loader: Callable[[Path | None], Any],
    backup_database: Callable[..., Any],
    restore_database: Callable[..., Any],
    unlock_database: Callable[..., Any],
    config_migrator: Callable[..., Any],
    inspect_database: Callable[..., Any],
    migrate_database: Callable[..., Any],
    verify_database: Callable[..., Any],
    reembed_database: Callable[..., Any],
    evaluation_runner: Callable[[Path, Path | None], Any],
) -> int:
    if args.command == "config":
        _print_json(
            config_migrator(
                args.source,
                out=args.out,
                dry_run=args.dry_run,
            )
        )
        return 0

    if args.command == "eval":
        try:
            result = evaluation_runner(args.path, args.config)
        except ModuleNotFoundError as exc:
            if exc.name and (exc.name == "evals" or exc.name.startswith("evals.")):
                parser.error(
                    "The eval command requires the repository eval modules. "
                    "Install from source with `pip install -e .[dev]` or run from the project root."
                )
            raise
        print(str(result).replace("\\", "/"))
        return 0

    if args.command == "database":
        settings = settings_loader(args.config)
        if args.database_command == "inspect":
            _print_json(
                inspect_database(
                    args.database_path or settings.database.path,
                    settings=settings,
                )
            )
            return 0

        if args.database_command == "backup":
            database_path = args.database_path or settings.database.path
            _print_json(
                backup_database(
                    database_path,
                    destination=args.out,
                    overwrite=args.overwrite,
                )
            )
            return 0

        if args.database_command == "restore":
            database_path = args.database_path or settings.database.path
            _print_json(
                restore_database(
                    database_path,
                    backup_path=args.backup_path,
                    overwrite=args.overwrite,
                )
            )
            return 0

        if args.database_command == "migrate":
            _print_json(
                migrate_database(
                    args.source_database_path or settings.database.path,
                    settings=settings,
                    target_database_path=args.target_database_path,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                    backup_destination=args.backup_path,
                    search_queries=args.search_query,
                    verification_scope=_optional_scope_from_args(args),
                )
            )
            return 0

        if args.database_command == "verify":
            _print_json(
                verify_database(
                    args.database_path or settings.database.path,
                    settings=settings,
                    search_queries=args.search_query,
                    scope=_optional_scope_from_args(args),
                )
            )
            return 0

        if args.database_command == "reembed":
            _print_json(
                reembed_database(
                    args.database_path or settings.database.path,
                    settings=settings,
                    scope=_optional_scope_from_args(args),
                    limit=args.limit,
                    dry_run=args.dry_run,
                )
            )
            return 0

    if args.command == "unlock":
        settings = settings_loader(args.config)
        database_path = args.database_path or settings.database.path
        _print_json(unlock_database(database_path, force=args.force))
        return 0

    if args.command == "backup":
        settings = settings_loader(args.config)
        database_path = args.database_path or settings.database.path
        _print_json(
            backup_database(
                database_path,
                destination=args.out,
                overwrite=args.overwrite,
            )
        )
        return 0

    if args.command == "restore":
        settings = settings_loader(args.config)
        database_path = args.database_path or settings.database.path
        _print_json(
            restore_database(
                database_path,
                backup_path=args.backup_path,
                overwrite=args.overwrite,
            )
        )
        return 0

    if args.command == "models":
        settings = settings_loader(args.config)
        if args.models_command == "list":
            _print_json(list_models(settings))
            return 0
        if args.models_command == "inspect":
            _print_json(inspect_model(settings, capability=args.capability))
            return 0
        if args.models_command == "verify":
            _print_json(verify_models(settings, capability=args.capability))
            return 0
        if args.models_command == "install":
            _print_json(
                install_model(
                    settings,
                    capability=args.capability,
                    source=args.source,
                    destination=args.destination,
                    force=args.force,
                )
            )
            return 0
        if args.models_command == "export-manifest":
            _print_json(
                export_model_manifest(
                    settings,
                    capability=args.capability,
                    out=args.out,
                )
            )
            return 0
        if args.models_command == "doctor":
            _print_json(doctor_models(settings, capability=args.capability))
            return 0

    store = store_factory(args.config)

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
            export_payload = _export_payload(store, _scope_from_args(args))
            args.out.write_text(_to_json(export_payload), encoding="utf-8")
            _print_json(export_payload)
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


def run_evaluation(path: Path, config_path: Path | None = None) -> Any:
    from evals.runner import run

    return run(path, config_path=config_path)


def _scope_from_args(args: argparse.Namespace) -> Scope:
    return Scope(
        user_id=getattr(args, "user_id", None),
        project_id=getattr(args, "project_id", None),
        agent_id=getattr(args, "agent_id", None),
    )


def _optional_scope_from_args(args: argparse.Namespace) -> Scope | None:
    scope = _scope_from_args(args)
    return None if scope.is_global else scope


def _export_payload(store: Any, scope: Scope) -> MemoryExport:
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