"""Thin command-line adapter over the shared service layer."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .arcade import backup_arcade_database, restore_arcade_database, unlock_arcade_database
from .cli_handlers import handle_command, run_evaluation
from .config import load_settings
from .config_migration import migrate_legacy_config
from .database_ops import inspect_database, migrate_database, reembed_database, verify_database
from .models import FolderIngestConnectionStrategy
from .store import MemoryStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bytebox")
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

    config = subparsers.add_parser("config", parents=[common])
    config_subparsers = config.add_subparsers(dest="config_command", required=True)

    migrate_config = config_subparsers.add_parser("migrate")
    migrate_config.add_argument("source", type=Path)
    migrate_config.add_argument("--out", type=Path, default=None)
    migrate_config.add_argument("--dry-run", action="store_true")

    database = subparsers.add_parser("database", parents=[common])
    database_subparsers = database.add_subparsers(dest="database_command", required=True)

    inspect_database_cmd = database_subparsers.add_parser("inspect")
    inspect_database_cmd.add_argument("--database-path", type=Path, default=None)

    database_backup = database_subparsers.add_parser("backup")
    database_backup.add_argument("--database-path", type=Path, default=None)
    database_backup.add_argument("--out", type=Path, default=None)
    database_backup.add_argument("--overwrite", action="store_true")

    database_restore = database_subparsers.add_parser("restore")
    database_restore.add_argument("backup_path", type=Path)
    database_restore.add_argument("--database-path", type=Path, default=None)
    database_restore.add_argument("--overwrite", action="store_true")

    database_migrate = database_subparsers.add_parser("migrate")
    database_migrate.add_argument("--source-database-path", type=Path, default=None)
    database_migrate.add_argument("--target-database-path", type=Path, default=None)
    database_migrate.add_argument("--backup-path", type=Path, default=None)
    database_migrate.add_argument("--overwrite", action="store_true")
    database_migrate.add_argument("--dry-run", action="store_true")
    database_migrate.add_argument("--search-query", action="append", default=[])
    _add_scope_arguments(database_migrate)

    database_verify = database_subparsers.add_parser("verify")
    database_verify.add_argument("--database-path", type=Path, default=None)
    database_verify.add_argument("--search-query", action="append", default=[])
    _add_scope_arguments(database_verify)

    database_reembed = database_subparsers.add_parser("reembed")
    database_reembed.add_argument("--database-path", type=Path, default=None)
    database_reembed.add_argument("--dry-run", action="store_true")
    database_reembed.add_argument("--limit", type=int, default=None)
    _add_scope_arguments(database_reembed)

    backup = subparsers.add_parser("backup", parents=[common])
    backup.add_argument("--database-path", type=Path, default=None)
    backup.add_argument("--out", type=Path, default=None)
    backup.add_argument("--overwrite", action="store_true")

    restore = subparsers.add_parser("restore", parents=[common])
    restore.add_argument("backup_path", type=Path)
    restore.add_argument("--database-path", type=Path, default=None)
    restore.add_argument("--overwrite", action="store_true")

    models = subparsers.add_parser("models", parents=[common])
    model_subparsers = models.add_subparsers(dest="models_command", required=True)

    model_subparsers.add_parser("list")

    inspect_model = model_subparsers.add_parser("inspect")
    inspect_model.add_argument(
        "--capability",
        choices=["embedding", "reranker"],
        default="embedding",
    )

    verify_model = model_subparsers.add_parser("verify")
    verify_model.add_argument(
        "--capability",
        choices=["embedding", "reranker"],
        default="embedding",
    )

    install_model = model_subparsers.add_parser("install")
    install_model.add_argument(
        "--capability",
        choices=["embedding", "reranker"],
        default="embedding",
    )
    install_model.add_argument("--source", type=Path, required=True)
    install_model.add_argument("--destination", type=Path, default=None)
    install_model.add_argument("--force", action="store_true")

    export_manifest = model_subparsers.add_parser("export-manifest")
    export_manifest.add_argument(
        "--capability",
        choices=["embedding", "reranker"],
        default="embedding",
    )
    export_manifest.add_argument("--out", type=Path, default=None)

    doctor = model_subparsers.add_parser("doctor")
    doctor.add_argument("--capability", choices=["embedding", "reranker"], default="embedding")

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
    return handle_command(
        args,
        parser,
        store_factory=lambda config_path: MemoryStore.from_config(config_path),
        settings_loader=load_settings,
        backup_database=backup_arcade_database,
        restore_database=restore_arcade_database,
        unlock_database=unlock_arcade_database,
        config_migrator=migrate_legacy_config,
        inspect_database=inspect_database,
        migrate_database=migrate_database,
        verify_database=verify_database,
        reembed_database=reembed_database,
        evaluation_runner=run_evaluation,
    )


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id")
    parser.add_argument("--project-id")
    parser.add_argument("--agent-id")


if __name__ == "__main__":
    raise SystemExit(main())
