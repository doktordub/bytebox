"""Lifecycle transition rules for mutable agent memories."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .arcade import ArcadeMemoryRepository
from .arcade.transactions import managed_transaction
from .errors import LifecycleError, MemoryNotFoundError
from .models import MemoryFeedback, MemoryRecord, MemoryStatus, MemoryType

_PROMOTION_CONFIDENCE_FLOOR = 0.75
_PROMOTION_IMPORTANCE_FLOOR = 0.60
_CONTRADICTION_CONFIDENCE_DELTA = 0.15
_POSITIVE_FEEDBACK_DELTA = 0.10
_NEGATIVE_FEEDBACK_DELTA = 0.10


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


class MemoryLifecycleManager:
	"""Coordinates auditable lifecycle transitions on top of the repository."""

	def __init__(self, repository: ArcadeMemoryRepository) -> None:
		self._repository = repository

	def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
		record = self._require_agent_memory(memory_id, operation="promote")
		self._ensure_transitionable(
			record,
			operation="promote",
			blocked={
				MemoryStatus.EXPIRED,
				MemoryStatus.FORGOTTEN,
				MemoryStatus.DELETED,
				MemoryStatus.REMOVED,
				MemoryStatus.SUPERSEDED,
			},
		)

		return self._persist_record(
			record,
			operation="promote",
			reason=reason,
			status=MemoryStatus.ACTIVE,
			confidence=max(record.confidence, _PROMOTION_CONFIDENCE_FLOOR),
			importance=max(record.importance, _PROMOTION_IMPORTANCE_FLOOR),
		)

	def supersede(
		self,
		old_memory_id: str,
		new_memory_id: str,
		reason: str | None = None,
	) -> None:
		if old_memory_id == new_memory_id:
			raise LifecycleError("A memory record cannot supersede itself.")

		old_record = self._require_agent_memory(old_memory_id, operation="supersede")
		new_record = self._require_agent_memory(new_memory_id, operation="supersede")
		self._ensure_transitionable(
			old_record,
			operation="supersede",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)
		self._ensure_transitionable(
			new_record,
			operation="supersede",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)

		with managed_transaction(self._repository.database):
			replaced = self._build_updated_record(
				old_record,
				operation="supersede",
				reason=reason,
				related_memory_id=new_memory_id,
				status=MemoryStatus.SUPERSEDED,
				superseded_by=new_memory_id,
				allow_retrieval=False,
			)
			replacement = self._build_updated_record(
				new_record,
				operation="supersede",
				reason=reason,
				related_memory_id=old_memory_id,
				status=MemoryStatus.ACTIVE,
				allow_retrieval=True,
			)

			self._repository._persist_existing(replaced, use_transaction=False)
			self._repository._persist_existing(replacement, use_transaction=False)
			self._repository._create_edge(
				new_memory_id,
				old_memory_id,
				"SUPERSEDES",
				properties=self._edge_properties(reason),
				use_transaction=False,
			)

	def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None:
		if memory_id_a == memory_id_b:
			raise LifecycleError("A memory record cannot contradict itself.")

		left = self._require_agent_memory(memory_id_a, operation="contradict")
		right = self._require_agent_memory(memory_id_b, operation="contradict")
		self._ensure_transitionable(
			left,
			operation="contradict",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)
		self._ensure_transitionable(
			right,
			operation="contradict",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)

		with managed_transaction(self._repository.database):
			contradicted_left = self._build_updated_record(
				left,
				operation="contradict",
				reason=reason,
				related_memory_id=memory_id_b,
				status=MemoryStatus.CONTRADICTED,
				confidence=max(0.0, left.confidence - _CONTRADICTION_CONFIDENCE_DELTA),
			)
			contradicted_right = self._build_updated_record(
				right,
				operation="contradict",
				reason=reason,
				related_memory_id=memory_id_a,
				status=MemoryStatus.CONTRADICTED,
				confidence=max(0.0, right.confidence - _CONTRADICTION_CONFIDENCE_DELTA),
			)

			self._repository._persist_existing(contradicted_left, use_transaction=False)
			self._repository._persist_existing(contradicted_right, use_transaction=False)
			self._repository._create_edge(
				memory_id_a,
				memory_id_b,
				"CONTRADICTS",
				properties=self._edge_properties(reason),
				use_transaction=False,
			)

	def expire(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
		record = self._require_agent_memory(memory_id, operation="expire")
		self._ensure_transitionable(
			record,
			operation="expire",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)

		expires_at = record.expires_at or _utcnow()
		return self._persist_record(
			record,
			operation="expire",
			reason=reason,
			status=MemoryStatus.EXPIRED,
			expires_at=expires_at,
			allow_retrieval=False,
		)

	def forget(self, memory_id: str, reason: str | None = None) -> MemoryRecord:
		record = self._require_agent_memory(memory_id, operation="forget")
		self._ensure_transitionable(
			record,
			operation="forget",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)

		return self._persist_record(
			record,
			operation="forget",
			reason=reason,
			status=MemoryStatus.FORGOTTEN,
			allow_retrieval=False,
			allow_llm_context=False,
		)

	def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord:
		record = self._require_agent_memory(memory_id, operation="add_feedback")
		self._ensure_transitionable(
			record,
			operation="add_feedback",
			blocked={MemoryStatus.FORGOTTEN, MemoryStatus.DELETED, MemoryStatus.REMOVED},
		)

		confidence = record.confidence
		importance = record.importance

		if feedback.positive is True:
			confidence = min(1.0, confidence + _POSITIVE_FEEDBACK_DELTA)
		elif feedback.positive is False:
			confidence = max(0.0, confidence - _NEGATIVE_FEEDBACK_DELTA)

		if feedback.confirmed is True:
			confidence = max(confidence, _PROMOTION_CONFIDENCE_FLOOR)
			importance = max(importance, _PROMOTION_IMPORTANCE_FLOOR)
		elif feedback.confirmed is False:
			confidence = max(0.0, confidence - _CONTRADICTION_CONFIDENCE_DELTA)

		if feedback.confidence is not None:
			confidence = feedback.confidence
		if feedback.importance is not None:
			importance = feedback.importance

		metadata = self._with_feedback_event(record.metadata, feedback)
		updates: dict[str, Any] = {
			"metadata": metadata,
			"confidence": confidence,
			"importance": importance,
		}
		if feedback.user_rating is not None:
			updates["user_rating"] = feedback.user_rating

		return self._persist_existing(record, **updates)

	def _require_agent_memory(self, memory_id: str, *, operation: str) -> MemoryRecord:
		record = self._repository.get_memory(memory_id)
		if record is None:
			raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")
		if record.memory_type == MemoryType.DOCUMENT_CHUNK:
			raise LifecycleError(
				f"{operation} is only supported for mutable agent memories; "
				"document chunks keep their deterministic ingestion lifecycle."
			)
		return record

	def _ensure_transitionable(
		self,
		record: MemoryRecord,
		*,
		operation: str,
		blocked: set[MemoryStatus],
	) -> None:
		if record.status in blocked:
			raise LifecycleError(
				f"Cannot {operation} memory {record.memory_id} while it is in status "
				f"'{record.status.value}'."
			)

	def _persist_record(
		self,
		record: MemoryRecord,
		*,
		operation: str,
		reason: str | None,
		related_memory_id: str | None = None,
		**updates: Any,
	) -> MemoryRecord:
		next_record = self._build_updated_record(
			record,
			operation=operation,
			reason=reason,
			related_memory_id=related_memory_id,
			**updates,
		)
		self._repository._persist_existing(next_record, use_transaction=True)
		return next_record

	def _persist_existing(self, record: MemoryRecord, **updates: Any) -> MemoryRecord:
		next_record = self._build_updated_record(record, operation="feedback", reason=None, **updates)
		self._repository._persist_existing(next_record, use_transaction=True)
		return next_record

	def _build_updated_record(
		self,
		record: MemoryRecord,
		*,
		operation: str,
		reason: str | None,
		related_memory_id: str | None = None,
		**updates: Any,
	) -> MemoryRecord:
		payload = record.model_dump(mode="python")
		payload.update(updates)
		metadata = payload.get("metadata", record.metadata)
		payload["metadata"] = self._with_lifecycle_event(
			metadata,
			operation=operation,
			reason=reason,
			related_memory_id=related_memory_id,
			status_before=record.status,
			status_after=payload.get("status", record.status),
		)
		payload["updated_at"] = _utcnow()
		payload["version"] = record.version + 1
		return MemoryRecord.model_validate(payload)

	def _with_lifecycle_event(
		self,
		metadata: dict[str, Any],
		*,
		operation: str,
		reason: str | None,
		related_memory_id: str | None,
		status_before: MemoryStatus,
		status_after: MemoryStatus,
	) -> dict[str, Any]:
		merged = deepcopy(metadata)
		events = list(merged.get("lifecycle_events", []))

		event: dict[str, Any] = {
			"operation": operation,
			"recorded_at": _utcnow().isoformat(),
			"status_before": status_before.value,
			"status_after": status_after.value,
		}
		if reason is not None:
			event["reason"] = reason
		if related_memory_id is not None:
			event["related_memory_id"] = related_memory_id

		events.append(event)
		merged["lifecycle_events"] = events
		merged["last_lifecycle_operation"] = operation
		return merged

	def _with_feedback_event(self, metadata: dict[str, Any], feedback: MemoryFeedback) -> dict[str, Any]:
		merged = deepcopy(metadata)
		events = list(merged.get("feedback_events", []))
		event = {"recorded_at": _utcnow().isoformat(), **feedback.model_dump(exclude_none=True)}
		events.append(event)
		merged["feedback_events"] = events
		merged["last_feedback_at"] = event["recorded_at"]
		return merged

	def _edge_properties(self, reason: str | None) -> dict[str, Any]:
		properties: dict[str, Any] = {"operation": "lifecycle"}
		if reason is not None:
			properties["reason"] = reason
		return properties
