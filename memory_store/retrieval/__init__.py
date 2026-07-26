"""Hybrid retrieval package."""

from .filters import (
	DEFAULT_EXCLUDED_STATUSES,
	HardFilter,
	NormalizedQuery,
	build_hard_filter,
	filter_records,
	is_record_retrievable,
	matches_hard_filters,
	normalize_query,
)
from .full_text import search_full_text
from .fusion import deduplicate_candidates, reciprocal_rank_fuse
from .graph import expand_one_hop_candidates
from .rerank import rerank_candidates
from .types import RetrievalCandidate, RetrievalMatch
from .vector import cosine_similarity, search_vectors

__all__ = [
	"DEFAULT_EXCLUDED_STATUSES",
	"HardFilter",
	"NormalizedQuery",
	"RetrievalCandidate",
	"RetrievalMatch",
	"build_hard_filter",
	"cosine_similarity",
	"deduplicate_candidates",
	"expand_one_hop_candidates",
	"filter_records",
	"is_record_retrievable",
	"matches_hard_filters",
	"normalize_query",
	"reciprocal_rank_fuse",
	"rerank_candidates",
	"search_full_text",
	"search_vectors",
]
