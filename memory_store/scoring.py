"""Scoring helpers for hybrid retrieval candidates."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from math import exp

from .config import TemporalScoringSettings
from .models import MemoryRecord, MemoryStatus, MemoryType, SourceType
from .retrieval.types import RetrievalCandidate

_PRENORMALIZED_COMPONENTS = frozenset({"temporal", "importance", "confidence", "user_rating"})
_OPTIONAL_COMPONENTS = frozenset({"user_rating"})
_RERANKER_REDISTRIBUTION = {
	"retrieval_fusion": 0.25,
	"vector": 0.12,
	"full_text": 0.08,
}


def enrich_candidate_scores(
	candidates: list[RetrievalCandidate],
	*,
	temporal_settings: TemporalScoringSettings,
	now: datetime | None = None,
) -> list[RetrievalCandidate]:
	current_time = now or datetime.now(timezone.utc)

	for candidate in candidates:
		temporal_score, temporal_debug = _compute_temporal_score(
			candidate.memory,
			temporal_settings=temporal_settings,
			now=current_time,
		)
		candidate.component_scores["temporal"] = temporal_score
		candidate.component_scores["importance"] = _clamp(candidate.memory.importance)
		candidate.component_scores["confidence"] = _clamp(candidate.memory.confidence)

		if candidate.memory.user_rating is None:
			candidate.component_scores.pop("user_rating", None)
		else:
			candidate.component_scores["user_rating"] = _clamp(candidate.memory.user_rating)

		candidate.debug["temporal_scoring"] = temporal_debug

	return candidates


def normalize_candidate_scores(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
	component_names = {
		component_name
		for candidate in candidates
		for component_name in candidate.component_scores
	}

	for candidate in candidates:
		candidate.normalized_scores = {}

	for component_name in sorted(component_names):
		values = [
			candidate.component_scores[component_name]
			for candidate in candidates
			if component_name in candidate.component_scores
		]
		if not values:
			continue

		if component_name in _PRENORMALIZED_COMPONENTS:
			for candidate in candidates:
				if component_name not in candidate.component_scores:
					continue
				candidate.normalized_scores[component_name] = _clamp(
					candidate.component_scores[component_name]
				)
			continue

		minimum = min(values)
		maximum = max(values)
		for candidate in candidates:
			if component_name not in candidate.component_scores:
				continue

			raw_score = candidate.component_scores[component_name]
			if maximum == minimum:
				normalized = 1.0 if raw_score > 0.0 else 0.0
			else:
				normalized = (raw_score - minimum) / (maximum - minimum)
			candidate.normalized_scores[component_name] = _clamp(normalized)

	return candidates


def score_candidates(
	candidates: list[RetrievalCandidate],
	weights: Mapping[str, float],
	*,
	reranker_enabled: bool,
) -> list[RetrievalCandidate]:
	effective_weights, redistribution = _effective_weights(
		weights,
		reranker_enabled=reranker_enabled,
	)
	total_effective_weight = sum(weight for weight in effective_weights.values() if weight > 0.0)

	for candidate in candidates:
		contributions: dict[str, dict[str, float | None]] = {}
		missing_components: list[dict[str, object]] = []
		numerator = 0.0
		denominator = 0.0

		for component_name, weight in effective_weights.items():
			if weight <= 0.0:
				continue

			raw_score = candidate.component_scores.get(component_name)
			normalized_score = candidate.normalized_scores.get(component_name)
			excluded_from_denominator = (
				normalized_score is None and component_name in _OPTIONAL_COMPONENTS
			)
			if normalized_score is None:
				missing_components.append(
					{
						"component": component_name,
						"reason": _missing_component_reason(
							candidate,
							component_name,
							reranker_enabled=reranker_enabled,
						),
						"excluded_from_denominator": excluded_from_denominator,
					}
				)
				if excluded_from_denominator:
					continue
				normalized_score = 0.0

			weighted_score = weight * normalized_score
			numerator += weighted_score
			denominator += weight
			contributions[component_name] = {
				"weight": weight,
				"raw_score": raw_score,
				"normalized_score": normalized_score,
				"weighted_score": weighted_score,
			}

		candidate.final_score = _clamp(numerator / denominator) if denominator > 0.0 else 0.0
		scoring_debug: dict[str, object] = {
			"weights": {name: float(weight) for name, weight in weights.items()},
			"effective_weights": effective_weights,
			"effective_weight_sum": total_effective_weight,
			"reranker_redistributed": bool(redistribution),
			"reranker_redistribution": redistribution,
			"contributions": contributions,
			"missing_components": missing_components,
			"score_numerator": numerator,
			"score_denominator": denominator,
			"available_components": sorted(candidate.normalized_scores),
		}
		temporal_debug = candidate.debug.get("temporal_scoring")
		if temporal_debug is not None:
			scoring_debug["temporal"] = temporal_debug
		candidate.debug["scoring"] = scoring_debug

	return candidates


def _effective_weights(
	weights: Mapping[str, float],
	*,
	reranker_enabled: bool,
) -> tuple[dict[str, float], dict[str, float]]:
	effective = {name: max(0.0, float(weight)) for name, weight in weights.items()}
	redistributed: dict[str, float] = {}
	reranker_weight = effective.get("reranker", 0.0)
	if reranker_enabled or reranker_weight <= 0.0:
		return effective, redistributed

	effective["reranker"] = 0.0
	redistribution_total = sum(_RERANKER_REDISTRIBUTION.values())
	if redistribution_total <= 0.0:
		return effective, redistributed

	for component_name, base_weight in _RERANKER_REDISTRIBUTION.items():
		additional_weight = reranker_weight * (base_weight / redistribution_total)
		effective[component_name] = effective.get(component_name, 0.0) + additional_weight
		redistributed[component_name] = additional_weight

	return effective, redistributed


def _compute_temporal_score(
	memory: MemoryRecord,
	*,
	temporal_settings: TemporalScoringSettings,
	now: datetime,
) -> tuple[float, dict[str, object]]:
	rule = getattr(temporal_settings, memory.memory_type.value)
	reference_time = _as_utc(memory.updated_at or memory.created_at)
	age_days = max((now - reference_time).total_seconds(), 0.0) / 86_400.0
	debug: dict[str, object] = {
		"memory_type": memory.memory_type.value,
		"behavior": rule.behavior,
		"half_life_days": rule.half_life_days,
		"age_days": age_days,
		"reference_time": reference_time.isoformat(),
	}

	if memory.memory_type == MemoryType.DOCUMENT_CHUNK:
		debug["reason"] = "document_chunk_no_decay"
		return 1.0, debug

	if memory.memory_type == MemoryType.DECISION:
		if memory.superseded_by or memory.status == MemoryStatus.SUPERSEDED:
			debug["reason"] = "decision_superseded"
			return 0.0, debug
		debug["reason"] = "decision_no_decay"
		return 1.0, debug

	if memory.memory_type == MemoryType.PROJECT_FACT and _is_source_controlled(memory):
		debug["reason"] = "source_controlled_project_fact"
		return 1.0, debug

	half_life_days = rule.half_life_days
	if half_life_days is None:
		debug["reason"] = "no_half_life_configured"
		return 1.0, debug

	debug["reason"] = "decay"
	return _clamp(exp(-(age_days / float(half_life_days)))), debug


def _missing_component_reason(
	candidate: RetrievalCandidate,
	component_name: str,
	*,
	reranker_enabled: bool,
) -> str:
	if component_name == "user_rating":
		return "not_provided"
	if component_name == "reranker" and not reranker_enabled:
		return "reranker_disabled"
	if component_name == "reranker" and not candidate.debug.get("rerank_applied", False):
		return "not_reranked"
	if component_name == "graph" and not candidate.debug.get("graph_expanded", False):
		return "no_graph_support"
	return "no_signal"


def _is_source_controlled(memory: MemoryRecord) -> bool:
	return memory.source_type == SourceType.DOCUMENT or any(
		(
			memory.source_path,
			memory.source_hash,
			memory.source_uri,
			memory.chunk_id,
		)
	)


def _as_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)


def _clamp(value: float) -> float:
	return max(0.0, min(1.0, float(value)))
