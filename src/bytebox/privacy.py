"""Privacy controls, portability helpers, and safe redaction flows."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .arcade import ArcadeMemoryRepository
from .arcade.schema import IMPORT_EXPORT_AUDIT_VERTEX, REDACTION_AUDIT_VERTEX
from .arcade.transactions import managed_transaction
from .config import PrivacySettings
from .errors import PrivacyError
from .lifecycle import MemoryLifecycleManager
from .models import (
	ImportMode,
	ImportResult,
	MemoryExport,
	MemoryImport,
	MemoryRecord,
	MemoryStatus,
	RedactionResult,
	Scope,
)

_REDACTION_TOKEN = "[REDACTED]"


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


class PrivacyController:
	"""Coordinates privacy-safe record mutations and import/export operations."""

	def __init__(
		self,
		repository: ArcadeMemoryRepository,
		*,
		lifecycle: MemoryLifecycleManager,
		settings: PrivacySettings,
	) -> None:
		self._repository = repository
		self._lifecycle = lifecycle
		self._settings = settings

	def forget(self, memory_id: str) -> MemoryRecord:
		return self._lifecycle.forget(memory_id)

	def forget_by_user(self, user_id: str) -> int:
		scope = Scope(user_id=user_id)
		records = [
			record
			for record in self._matching_records(scope)
			if record.status
			not in {MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED}
		]
		if not records:
			return 0

		with managed_transaction(self._repository.database):
			for record in records:
				updated = self._build_updated_record(
					record,
					operation="forget_by_user",
					scope=scope,
					details={"user_id": user_id},
					status=MemoryStatus.FORGOTTEN,
					allow_retrieval=False,
					allow_llm_context=False,
				)
				self._repository._persist_existing(updated, use_transaction=False)

		return len(records)

	def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int:
		records = self._matching_records(scope)
		if not records:
			return 0

		memory_ids = [record.memory_id for record in records]
		if hard_delete:
			with managed_transaction(self._repository.database):
				self._write_audit_vertex(
					IMPORT_EXPORT_AUDIT_VERTEX,
					operation="delete_by_scope",
					scope=scope,
					details={
						"count": len(records),
						"hard_delete": True,
						"memory_ids": memory_ids,
					},
				)
				for record in records:
					self._repository._delete_memory(record.memory_id, use_transaction=False)
			return len(records)

		mutable_records = [record for record in records if record.status != MemoryStatus.DELETED]
		if not mutable_records:
			return 0

		with managed_transaction(self._repository.database):
			for record in mutable_records:
				updated = self._build_updated_record(
					record,
					operation="delete_by_scope",
					scope=scope,
					details={"hard_delete": False},
					status=MemoryStatus.DELETED,
					allow_retrieval=False,
					allow_llm_context=False,
				)
				self._repository._persist_existing(updated, use_transaction=False)

			self._write_audit_vertex(
				IMPORT_EXPORT_AUDIT_VERTEX,
				operation="delete_by_scope",
				scope=scope,
				details={
					"count": len(mutable_records),
					"hard_delete": False,
					"memory_ids": [record.memory_id for record in mutable_records],
				},
			)

		return len(mutable_records)

	def disable_memory(self, scope: Scope) -> int:
		records = [record for record in self._matching_records(scope) if record.allow_retrieval]
		if not records:
			return 0

		with managed_transaction(self._repository.database):
			for record in records:
				updated = self._build_updated_record(
					record,
					operation="disable_memory",
					scope=scope,
					details={},
					allow_retrieval=False,
				)
				self._repository._persist_existing(updated, use_transaction=False)

		return len(records)

	def export_user_memories(self, user_id: str) -> list[MemoryRecord]:
		scope = Scope(user_id=user_id)
		records = self._matching_records(scope)
		details = {
			"count": len(records),
			"memory_ids": [record.memory_id for record in records],
		}
		with managed_transaction(self._repository.database):
			self._write_audit_vertex(
				IMPORT_EXPORT_AUDIT_VERTEX,
				operation="export_user_memories",
				scope=scope,
				details=details,
			)
		return records

	def export_scope(self, scope: Scope) -> MemoryExport:
		records = self._matching_records(scope)
		details = {
			"count": len(records),
			"memory_ids": [record.memory_id for record in records],
		}
		with managed_transaction(self._repository.database):
			self._write_audit_vertex(
				IMPORT_EXPORT_AUDIT_VERTEX,
				operation="export_scope",
				scope=scope,
				details=details,
			)
		return MemoryExport(scope=scope, records=records)

	def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult:
		result = ImportResult()
		inserted_ids: list[str] = []
		updated_ids: list[str] = []

		with managed_transaction(self._repository.database):
			for incoming in payload.records:
				existing = self._repository.get_memory(incoming.memory_id)
				existing_by_key = None
				if incoming.stable_key is not None:
					existing_by_key = self._repository.get_by_stable_key(incoming.stable_key)
					if (
						existing_by_key is not None
						and existing_by_key.memory_id == incoming.memory_id
					):
						existing_by_key = None

				target = existing or existing_by_key

				if target is None:
					if mode == "replace":
						result.skipped += 1
						result.errors.append(
							f"Cannot replace missing memory '{incoming.memory_id}'."
						)
						continue

					self._repository._insert_memory(incoming, use_transaction=False)
					result.inserted += 1
					inserted_ids.append(incoming.memory_id)
					continue

				if mode == "insert":
					result.skipped += 1
					conflict = incoming.stable_key or incoming.memory_id
					result.errors.append(f"Import conflict for '{conflict}'.")
					continue

				merged = self._merge_import_record(target, incoming)
				self._repository._persist_existing(merged, use_transaction=False)
				result.updated += 1
				updated_ids.append(merged.memory_id)

			self._write_audit_vertex(
				IMPORT_EXPORT_AUDIT_VERTEX,
				operation="import_memories",
				scope=None,
				details={
					"source": payload.source,
					"mode": mode,
					"inserted": result.inserted,
					"updated": result.updated,
					"skipped": result.skipped,
					"memory_ids": sorted({*inserted_ids, *updated_ids}),
				},
			)

		return result

	def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult:
		if not patterns:
			raise PrivacyError("At least one redaction pattern is required.")

		try:
			compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
		except re.error as exc:
			raise PrivacyError(f"Invalid redaction pattern: {exc}") from exc

		records = self._matching_records(scope)
		redacted_ids: list[str] = []
		field_counts: dict[str, int] = {}

		with managed_transaction(self._repository.database):
			for record in records:
				updated, redacted_fields = self._redact_record(record, compiled_patterns, scope)
				if updated is None:
					continue

				self._repository._persist_existing(updated, use_transaction=False)
				redacted_ids.append(updated.memory_id)
				for field_name in redacted_fields:
					field_counts[field_name] = field_counts.get(field_name, 0) + 1

			if redacted_ids:
				self._write_audit_vertex(
					REDACTION_AUDIT_VERTEX,
					operation="redact",
					scope=scope,
					details={
						"count": len(redacted_ids),
						"memory_ids": redacted_ids,
						"pattern_count": len(compiled_patterns),
						"field_counts": field_counts,
					},
				)

		return RedactionResult(
			redacted=len(redacted_ids),
			patterns=patterns,
			memory_ids=redacted_ids,
		)

	def _matching_records(self, scope: Scope | None) -> list[MemoryRecord]:
		records = self._repository.list_matching_scope(scope)
		return sorted(records, key=lambda record: record.memory_id)

	def _merge_import_record(self, existing: MemoryRecord, incoming: MemoryRecord) -> MemoryRecord:
		payload = incoming.model_dump(mode="python")
		payload.update(
			{
				"memory_id": existing.memory_id,
				"created_at": min(existing.created_at, incoming.created_at),
				"updated_at": _utcnow(),
				"version": max(existing.version + 1, incoming.version),
			}
		)
		return MemoryRecord.model_validate(payload)

	def _redact_record(
		self,
		record: MemoryRecord,
		patterns: list[re.Pattern[str]],
		scope: Scope | None,
	) -> tuple[MemoryRecord | None, list[str]]:
		field_updates: dict[str, Any] = {}
		redacted_fields: list[str] = []

		for field_name in ("title", "summary", "text", "source_uri"):
			value = getattr(record, field_name)
			redacted, changed = _redact_value(value, patterns)
			if changed:
				field_updates[field_name] = redacted
				redacted_fields.append(field_name)

		redacted_tags, tags_changed = _redact_value(record.tags, patterns)
		if tags_changed:
			field_updates["tags"] = redacted_tags
			redacted_fields.append("tags")

		redacted_metadata, metadata_changed = _redact_value(record.metadata, patterns)
		if metadata_changed:
			field_updates["metadata"] = redacted_metadata
			redacted_fields.append("metadata")

		if not redacted_fields:
			return None, []

		details = {
			"pattern_count": len(patterns),
			"redacted_fields": redacted_fields,
			"embedding_cleared": record.embedding is not None,
		}
		field_updates.update(
			{
				"embedding": None,
				"embedding_model": None,
				"embedding_model_version": None,
				"embedding_dim": None,
				"embedding_created_at": None,
			}
		)
		updated = self._build_updated_record(
			record,
			operation="redact",
			scope=scope,
			details=details,
			**field_updates,
		)
		return updated, redacted_fields

	def _build_updated_record(
		self,
		record: MemoryRecord,
		*,
		operation: str,
		scope: Scope | None,
		details: dict[str, Any],
		**updates: Any,
	) -> MemoryRecord:
		payload = record.model_dump(mode="python")
		payload.update(updates)
		metadata = payload.get("metadata", record.metadata)
		payload["metadata"] = self._with_privacy_event(
			metadata,
			operation=operation,
			scope=scope,
			details=details,
			status_before=record.status,
			status_after=payload.get("status", record.status),
		)
		payload["updated_at"] = _utcnow()
		payload["version"] = record.version + 1
		return MemoryRecord.model_validate(payload)

	def _with_privacy_event(
		self,
		metadata: dict[str, Any],
		*,
		operation: str,
		scope: Scope | None,
		details: dict[str, Any],
		status_before: MemoryStatus,
		status_after: MemoryStatus,
	) -> dict[str, Any]:
		merged = deepcopy(metadata)
		events = list(merged.get("privacy_events", []))
		event = {
			"operation": operation,
			"recorded_at": _utcnow().isoformat(),
			"scope": _scope_key(scope),
			"status_before": status_before.value,
			"status_after": status_after.value,
			**details,
		}
		events.append(event)
		merged["privacy_events"] = events
		merged["last_privacy_operation"] = operation
		return merged

	def _write_audit_vertex(
		self,
		vertex_type: str,
		*,
		operation: str,
		scope: Scope | None,
		details: dict[str, Any],
	) -> None:
		vertex = self._repository.database.new_vertex(vertex_type)
		vertex.set("operation", operation)
		vertex.set("scope_key", _scope_key(scope))
		vertex.set("memory_id", None)
		vertex.set("details", details)
		vertex.set("created_at", _utcnow())
		vertex.save()


def _scope_key(scope: Scope | None) -> str:
	if scope is None:
		return "all"
	if scope.is_global:
		return "global"
	parts: list[str] = []
	if scope.user_id is not None:
		parts.append(f"user:{scope.user_id}")
	if scope.project_id is not None:
		parts.append(f"project:{scope.project_id}")
	if scope.agent_id is not None:
		parts.append(f"agent:{scope.agent_id}")
	return "|".join(parts)


def _redact_value(value: Any, patterns: list[re.Pattern[str]]) -> tuple[Any, bool]:
	if isinstance(value, str):
		redacted = value
		changed = False
		for pattern in patterns:
			redacted, replacements = pattern.subn(_REDACTION_TOKEN, redacted)
			changed = changed or replacements > 0
		return redacted, changed

	if isinstance(value, list):
		changed = False
		items: list[Any] = []
		for item in value:
			redacted_item, item_changed = _redact_value(item, patterns)
			items.append(redacted_item)
			changed = changed or item_changed
		return items, changed

	if isinstance(value, dict):
		changed = False
		redacted_dict: dict[str, Any] = {}
		for key, item in value.items():
			redacted_item, item_changed = _redact_value(item, patterns)
			redacted_dict[key] = redacted_item
			changed = changed or item_changed
		return redacted_dict, changed

	return value, False
