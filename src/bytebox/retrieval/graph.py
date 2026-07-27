"""Optional one-hop graph expansion for retrieval candidates."""

from __future__ import annotations

from collections.abc import Callable

from ..models import MemoryRecord
from .filters import HardFilter, matches_hard_filters
from .types import RetrievalCandidate


def expand_one_hop_candidates(
	candidates: list[RetrievalCandidate],
	*,
	hard_filter: HardFilter,
	read_links: Callable[[str], list[tuple[str, MemoryRecord]]],
) -> list[RetrievalCandidate]:
	seed_candidates = list(candidates)
	by_memory_id = {candidate.memory.memory_id: candidate for candidate in candidates}

	for candidate in seed_candidates:
		base_score = candidate.component_scores.get("retrieval_fusion", 0.0)
		if base_score <= 0.0:
			base_score = max(
				candidate.component_scores.get("vector", 0.0),
				candidate.component_scores.get("full_text", 0.0),
			)
		if base_score <= 0.0:
			continue

		for edge_type, neighbor in read_links(candidate.memory.memory_id):
			if neighbor.memory_id == candidate.memory.memory_id:
				continue
			if not matches_hard_filters(neighbor, hard_filter):
				continue

			expanded = by_memory_id.get(neighbor.memory_id)
			if expanded is None:
				expanded = RetrievalCandidate(
					memory=neighbor,
					debug={
						"sources": ["graph"],
						"source_ranks": {},
						"duplicate_memory_ids": [],
					},
				)
				by_memory_id[neighbor.memory_id] = expanded
				candidates.append(expanded)
			elif "graph" not in expanded.debug.setdefault("sources", []):
				expanded.debug["sources"].append("graph")

			expanded.component_scores["graph"] = (
				expanded.component_scores.get("graph", 0.0) + base_score
			)
			expanded.debug["graph_expanded"] = True
			sources = expanded.debug.setdefault("graph_sources", [])
			source = {"memory_id": candidate.memory.memory_id, "edge_type": edge_type}
			if source not in sources:
				sources.append(source)

	return candidates
