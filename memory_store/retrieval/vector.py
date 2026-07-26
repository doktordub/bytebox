"""Dense vector retrieval helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import sqrt

from ..models import MemoryRecord
from .filters import NormalizedQuery
from .types import RetrievalMatch


def search_vectors(
	records: list[MemoryRecord],
	query: NormalizedQuery,
	*,
	top_n: int,
	embed_query: Callable[[str], Sequence[float]],
) -> list[RetrievalMatch]:
	if top_n < 1:
		return []

	vector_records = [record for record in records if record.embedding is not None]
	if not vector_records:
		return []

	query_vector = [float(value) for value in embed_query(query.text)]
	matches: list[RetrievalMatch] = []
	for record in vector_records:
		embedding = record.embedding
		if embedding is None:
			continue
		score = cosine_similarity(query_vector, embedding)
		if score <= 0.0:
			continue
		matches.append(RetrievalMatch(memory=record, score=score, source="vector"))

	matches.sort(key=lambda match: (-match.score, match.memory.memory_id))
	return matches[:top_n]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	if not left or not right or len(left) != len(right):
		return 0.0

	left_norm = sqrt(sum(float(value) * float(value) for value in left))
	right_norm = sqrt(sum(float(value) * float(value) for value in right))
	if left_norm == 0.0 or right_norm == 0.0:
		return 0.0

	dot_product = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
	return dot_product / (left_norm * right_norm)
