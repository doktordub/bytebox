"""Lifecycle-aware retrieval filters shared by hybrid retrieval phases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models import (
	MemoryRecord,
	MemorySearchQuery,
	MemoryStatus,
	MemoryType,
	Scope,
	SensitivityLevel,
	SourceType,
)

DEFAULT_EXCLUDED_STATUSES = frozenset(
	{
		MemoryStatus.EXPIRED,
		MemoryStatus.FORGOTTEN,
		MemoryStatus.DELETED,
		MemoryStatus.REMOVED,
	}
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class NormalizedQuery:
	text: str
	lowered: str
	tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HardFilter:
	scope: Scope
	memory_types: frozenset[MemoryType] | None
	statuses: frozenset[MemoryStatus]
	source_types: frozenset[SourceType] | None
	sensitivity: SensitivityLevel | None
	include_removed: bool
	include_forgotten: bool
	allow_retrieval_only: bool


def normalize_query(query: MemorySearchQuery | str) -> NormalizedQuery:
	raw_text = query.text if isinstance(query, MemorySearchQuery) else query
	cleaned = " ".join(raw_text.split()).strip()
	lowered = cleaned.casefold()
	tokens = tuple(_TOKEN_PATTERN.findall(lowered))
	if not tokens and lowered:
		tokens = (lowered,)
	return NormalizedQuery(text=cleaned, lowered=lowered, tokens=tokens)


def build_hard_filter(query: MemorySearchQuery) -> HardFilter:
	default_statuses = {MemoryStatus.ACTIVE}
	if query.include_removed:
		default_statuses.update({MemoryStatus.DELETED, MemoryStatus.REMOVED})
	if query.include_forgotten:
		default_statuses.add(MemoryStatus.FORGOTTEN)

	return HardFilter(
		scope=query.scope,
		memory_types=frozenset(query.memory_types) if query.memory_types is not None else None,
		statuses=(
			frozenset(query.statuses)
			if query.statuses is not None
			else frozenset(default_statuses)
		),
		source_types=frozenset(query.source_types) if query.source_types is not None else None,
		sensitivity=query.sensitivity,
		include_removed=query.include_removed,
		include_forgotten=query.include_forgotten,
		allow_retrieval_only=query.allow_retrieval_only,
	)


def is_record_retrievable(
	record: MemoryRecord,
	*,
	now: datetime | None = None,
	include_removed: bool = False,
	include_forgotten: bool = False,
	allow_retrieval_only: bool = True,
) -> bool:
	effective_now = now or datetime.now(timezone.utc)

	if allow_retrieval_only and not record.allow_retrieval:
		return False
	if record.expires_at is not None and record.expires_at <= effective_now:
		return False
	if record.status == MemoryStatus.EXPIRED:
		return False
	if record.status == MemoryStatus.FORGOTTEN and not include_forgotten:
		return False
	if record.status in {MemoryStatus.DELETED, MemoryStatus.REMOVED} and not include_removed:
		return False
	return True


def filter_records(records: list[MemoryRecord], query: MemorySearchQuery) -> list[MemoryRecord]:
	hard_filter = build_hard_filter(query)
	return [
		record
		for record in records
		if matches_hard_filters(record, hard_filter)
	]


def matches_hard_filters(record: MemoryRecord, hard_filter: HardFilter) -> bool:
	if hard_filter.memory_types is not None and record.memory_type not in hard_filter.memory_types:
		return False
	if record.status not in hard_filter.statuses:
		return False
	if hard_filter.source_types is not None and record.source_type not in hard_filter.source_types:
		return False
	if hard_filter.sensitivity is None:
		if record.sensitivity == SensitivityLevel.SENSITIVE:
			return False
	elif record.sensitivity != hard_filter.sensitivity:
		return False
	return is_record_retrievable(
		record,
		include_removed=hard_filter.include_removed,
		include_forgotten=hard_filter.include_forgotten,
		allow_retrieval_only=hard_filter.allow_retrieval_only,
	)
