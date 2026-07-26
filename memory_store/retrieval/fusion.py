"""Fusion and deduplication for hybrid retrieval candidates."""

from __future__ import annotations

from .types import RetrievalCandidate, RetrievalMatch


def reciprocal_rank_fuse(
	*,
	vector_matches: list[RetrievalMatch],
	full_text_matches: list[RetrievalMatch],
	rrf_k: int,
) -> list[RetrievalCandidate]:
	candidates_by_memory_id: dict[str, RetrievalCandidate] = {}

	for source_name, matches in (("vector", vector_matches), ("full_text", full_text_matches)):
		for rank, match in enumerate(matches, start=1):
			candidate = candidates_by_memory_id.get(match.memory.memory_id)
			if candidate is None:
				candidate = RetrievalCandidate(
					memory=match.memory,
					debug={
						"sources": [],
						"source_ranks": {},
						"duplicate_memory_ids": [],
					},
				)
				candidates_by_memory_id[match.memory.memory_id] = candidate

			candidate.component_scores[source_name] = match.score
			candidate.component_scores["retrieval_fusion"] = (
				candidate.component_scores.get("retrieval_fusion", 0.0)
				+ (1.0 / (rrf_k + rank))
			)
			candidate.debug["source_ranks"][source_name] = rank
			if source_name not in candidate.debug["sources"]:
				candidate.debug["sources"].append(source_name)

	ordered = sorted(
		candidates_by_memory_id.values(),
		key=lambda candidate: (
			-candidate.component_scores.get("retrieval_fusion", 0.0),
			candidate.memory.memory_id,
		),
	)
	for candidate in ordered:
		candidate.debug["sources"].sort(
			key=lambda source_name: candidate.debug["source_ranks"].get(source_name, 10_000)
		)
	return ordered


def deduplicate_candidates(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
	deduplicated: list[RetrievalCandidate] = []
	owner_by_key: dict[str, int] = {}

	for candidate in candidates:
		keys = _candidate_keys(candidate)
		existing_index = next((owner_by_key[key] for key in keys if key in owner_by_key), None)
		if existing_index is None:
			owner_index = len(deduplicated)
			deduplicated.append(candidate)
			for key in keys:
				owner_by_key[key] = owner_index
			continue

		owner = deduplicated[existing_index]
		_merge_candidate(owner, candidate)
		for key in keys:
			owner_by_key[key] = existing_index

	return deduplicated


def _candidate_keys(candidate: RetrievalCandidate) -> list[str]:
	record = candidate.memory
	keys = [f"memory_id:{record.memory_id}"]
	if record.stable_key:
		keys.append(f"stable_key:{record.stable_key}")
	if record.chunk_id:
		keys.append(f"chunk_id:{record.chunk_id}")
	if record.source_hash:
		keys.append(f"source_hash:{record.source_hash}")
	return keys


def _merge_candidate(owner: RetrievalCandidate, duplicate: RetrievalCandidate) -> None:
	for component_name, score in duplicate.component_scores.items():
		owner.component_scores[component_name] = max(
			owner.component_scores.get(component_name, 0.0),
			score,
		)

	owner_sources = owner.debug.setdefault("sources", [])
	duplicate_sources = duplicate.debug.get("sources", [])
	for source_name in duplicate_sources:
		if source_name not in owner_sources:
			owner_sources.append(source_name)

	owner_ranks = owner.debug.setdefault("source_ranks", {})
	duplicate_ranks = duplicate.debug.get("source_ranks", {})
	for source_name, rank in duplicate_ranks.items():
		current = owner_ranks.get(source_name)
		owner_ranks[source_name] = rank if current is None else min(current, rank)

	duplicates = owner.debug.setdefault("duplicate_memory_ids", [])
	if (
		duplicate.memory.memory_id != owner.memory.memory_id
		and duplicate.memory.memory_id not in duplicates
	):
		duplicates.append(duplicate.memory.memory_id)

	if duplicate.debug.get("graph_expanded"):
		owner.debug["graph_expanded"] = True
		sources = owner.debug.setdefault("graph_sources", [])
		for source in duplicate.debug.get("graph_sources", []):
			if source not in sources:
				sources.append(source)
