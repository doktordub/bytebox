"""Schema versioning and idempotent ArcadeDB migrations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .connection import backup_arcade_database, normalize_database_path
from ..errors import PersistenceError, SchemaMismatchError
from .schema import MEMORY_RECORD_VERTEX, MIGRATION_RECORD_VERTEX, SCHEMA_VERSION_VERTEX, ensure_schema

_DOCUMENT_CHUNK_MEMORY_TYPE = "document_chunk"
_UNKNOWN_SORT_INDEX = 1_000_000_000


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Migration:
	version: int
	name: str
	apply: Callable[[Any, int], None]


@dataclass(frozen=True, slots=True)
class SchemaMigrationResult:
	database_path: Path
	previous_version: int
	schema_version: int
	target_version: int
	supported_version: int
	needs_migration: bool
	dry_run: bool
	backup_path: Path | None = None


def default_migrations() -> tuple[Migration, ...]:
	return (
		Migration(version=1, name="phase_2_persistence_foundation", apply=_apply_phase_two),
	)


def read_schema_version(database: Any) -> int:
	if not database.schema.exists_type(SCHEMA_VERSION_VERTEX):
		return 0

	result = database.query(
		"sql",
		f"SELECT version FROM {SCHEMA_VERSION_VERTEX} ORDER BY version DESC LIMIT 1",
	).first()
	if result is None:
		return 0
	version = result.get("version")
	return int(version) if version is not None else 0


def validate_schema_version(database: Any, expected_version: int) -> int:
	supported_version = max(migration.version for migration in default_migrations())
	if expected_version > supported_version:
		raise SchemaMismatchError(
			f"Configured schema version {expected_version} exceeds package support {supported_version}."
		)

	stored_version = read_schema_version(database)
	if stored_version > expected_version:
		raise SchemaMismatchError(
			f"Database schema version {stored_version} is newer than supported version "
			f"{expected_version}."
		)
	return stored_version


def plan_database_migrations(
	database: Any,
	*,
	database_path: str | Path,
	expected_version: int,
) -> SchemaMigrationResult:
	current_version = validate_schema_version(database, expected_version)
	supported_version = max(migration.version for migration in default_migrations())
	resolved_path = normalize_database_path(database_path)
	return SchemaMigrationResult(
		database_path=resolved_path,
		previous_version=current_version,
		schema_version=current_version,
		target_version=expected_version,
		supported_version=supported_version,
		needs_migration=current_version < expected_version,
		dry_run=True,
	)


def run_database_migrations(
	database: Any,
	*,
	database_path: str | Path,
	expected_version: int,
	embedding_dimensions: int,
	dry_run: bool = False,
	create_backup: bool = False,
	backup_destination: str | Path | None = None,
) -> SchemaMigrationResult:
	plan = plan_database_migrations(
		database,
		database_path=database_path,
		expected_version=expected_version,
	)
	if dry_run:
		return plan

	backup_path: Path | None = None
	if create_backup and plan.needs_migration and plan.database_path.exists():
		backup_summary = backup_arcade_database(
			plan.database_path,
			destination=backup_destination,
		)
		backup_path = normalize_database_path(backup_summary["backup_path"])

	schema_version = ensure_database_schema(
		database,
		expected_version=expected_version,
		embedding_dimensions=embedding_dimensions,
	)
	return SchemaMigrationResult(
		database_path=plan.database_path,
		previous_version=plan.previous_version,
		schema_version=schema_version,
		target_version=expected_version,
		supported_version=plan.supported_version,
		needs_migration=plan.needs_migration,
		dry_run=False,
		backup_path=backup_path,
	)


def ensure_database_schema(
	database: Any,
	*,
	expected_version: int,
	embedding_dimensions: int,
) -> int:
	current_version = validate_schema_version(database, expected_version)
	applied_versions = read_applied_migration_versions(database)

	for migration in default_migrations():
		if migration.version > expected_version:
			continue
		if current_version >= migration.version or migration.version in applied_versions:
			continue

		migration.apply(database, embedding_dimensions)
		_record_migration(database, migration)
		_upsert_schema_version(database, version=migration.version)
		current_version = migration.version

	ensure_schema(
		database,
		schema_version=expected_version,
		embedding_dimensions=embedding_dimensions,
	)
	_backfill_document_chunk_positions(database)

	return validate_schema_version(database, expected_version)


def read_applied_migration_versions(database: Any) -> set[int]:
	if not database.schema.exists_type(MIGRATION_RECORD_VERTEX):
		return set()

	results = database.query("sql", f"SELECT version FROM {MIGRATION_RECORD_VERTEX}")
	return {int(row.get("version")) for row in results if row.get("version") is not None}


def _apply_phase_two(database: Any, embedding_dimensions: int) -> None:
	ensure_schema(
		database,
		schema_version=1,
		embedding_dimensions=embedding_dimensions,
	)
	_backfill_document_chunk_positions(database)


def _backfill_document_chunk_positions(database: Any) -> None:
	if not database.schema.exists_type(MEMORY_RECORD_VERTEX):
		return

	try:
		rows = [
			row.to_dict()
			for row in database.query(
				"sql",
				(
					f"SELECT memory_id, source_path, user_id, project_id, agent_id, "
					"chunk_index, section_index, section_chunk_index, document_chunk_index, "
					f"metadata FROM {MEMORY_RECORD_VERTEX} WHERE memory_type = ?"
				),
				_DOCUMENT_CHUNK_MEMORY_TYPE,
			)
		]
	except Exception as exc:
		raise PersistenceError(
			f"Failed to read document chunks for position backfill: {exc}"
		) from exc

	if not rows:
		return

	grouped_rows: dict[tuple[str | None, str | None, str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
	for row in rows:
		grouped_rows[
			(
				_none_if_missing(row.get("user_id")),
				_none_if_missing(row.get("project_id")),
				_none_if_missing(row.get("agent_id")),
				_none_if_missing(row.get("source_path")),
			)
		].append(dict(row))

	pending_updates: list[tuple[str, dict[str, Any]]] = []
	for group_rows in grouped_rows.values():
		normalized_rows: list[tuple[dict[str, Any], dict[str, Any], int | None, int | None]] = []
		can_assign_document_indices = True
		for row in group_rows:
			metadata = _coerce_metadata(row.get("metadata"))
			section_index = _coerce_optional_int(row.get("section_index"), metadata.get("section_index"))
			section_chunk_index = _coerce_optional_int(
				row.get("section_chunk_index"),
				row.get("chunk_index"),
				metadata.get("section_chunk_index"),
			)
			if section_index is None or section_chunk_index is None:
				can_assign_document_indices = False
			normalized_rows.append((row, metadata, section_index, section_chunk_index))

		normalized_rows.sort(
			key=lambda item: (
				item[2] if item[2] is not None else _UNKNOWN_SORT_INDEX,
				item[3] if item[3] is not None else _UNKNOWN_SORT_INDEX,
				str(item[0].get("memory_id") or ""),
			)
		)

		for document_chunk_index, (row, metadata, section_index, section_chunk_index) in enumerate(normalized_rows):
			updates: dict[str, Any] = {}
			updated_metadata = dict(metadata)

			if section_index is not None and row.get("section_index") != section_index:
				updates["section_index"] = section_index
			if section_chunk_index is not None and row.get("section_chunk_index") != section_chunk_index:
				updates["section_chunk_index"] = section_chunk_index
			if section_chunk_index is not None and row.get("chunk_index") != section_chunk_index:
				updates["chunk_index"] = section_chunk_index
			if can_assign_document_indices and row.get("document_chunk_index") != document_chunk_index:
				updates["document_chunk_index"] = document_chunk_index

			if section_index is not None and updated_metadata.get("section_index") != section_index:
				updated_metadata["section_index"] = section_index
			if (
				section_chunk_index is not None
				and updated_metadata.get("section_chunk_index") != section_chunk_index
			):
				updated_metadata["section_chunk_index"] = section_chunk_index
			if (
				can_assign_document_indices
				and updated_metadata.get("document_chunk_index") != document_chunk_index
			):
				updated_metadata["document_chunk_index"] = document_chunk_index

			if updated_metadata != metadata:
				updates["metadata"] = updated_metadata

			memory_id = row.get("memory_id")
			if memory_id is not None and updates:
				pending_updates.append((str(memory_id), updates))

	if not pending_updates:
		return

	try:
		with database.transaction():
			for memory_id, updates in pending_updates:
				record = database.lookup_by_key(MEMORY_RECORD_VERTEX, ["memory_id"], [memory_id])
				if record is None:
					continue
				mutable = record.modify()
				for key, value in updates.items():
					mutable.set(key, value)
				mutable.save()
	except Exception as exc:
		raise PersistenceError(f"Failed to backfill document chunk positions: {exc}") from exc


def _coerce_metadata(value: Any) -> dict[str, Any]:
	if isinstance(value, Mapping):
		return dict(value)
	return {}


def _coerce_optional_int(*values: Any) -> int | None:
	for value in values:
		if value is None:
			continue
		try:
			return int(value)
		except (TypeError, ValueError):
			continue
	return None


def _none_if_missing(value: Any) -> str | None:
	if value is None:
		return None
	return str(value)


def _record_migration(database: Any, migration: Migration) -> None:
	try:
		with database.transaction():
			record = database.new_vertex(MIGRATION_RECORD_VERTEX)
			record.set("version", migration.version)
			record.set("name", migration.name)
			record.set("applied_at", _utcnow())
			record.save()
	except Exception as exc:
		raise PersistenceError(
			f"Failed to record migration {migration.version} ({migration.name}): {exc}"
		) from exc


def _upsert_schema_version(database: Any, *, version: int) -> None:
	try:
		existing = database.lookup_by_key(SCHEMA_VERSION_VERTEX, ["key"], ["active"])
		with database.transaction():
			if existing is None:
				record = database.new_vertex(SCHEMA_VERSION_VERTEX)
				record.set("key", "active")
			else:
				record = existing.modify()

			record.set("version", version)
			record.set("min_compatible_version", 1)
			record.set("updated_at", _utcnow())
			record.save()
	except Exception as exc:
		raise PersistenceError(f"Failed to write schema version metadata: {exc}") from exc
