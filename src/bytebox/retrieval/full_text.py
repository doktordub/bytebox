"""Local BM25-style full-text retrieval helpers."""

from __future__ import annotations

from collections import Counter
from math import log

from ..models import MemoryRecord
from .filters import NormalizedQuery, normalize_query
from .types import RetrievalMatch

_K1 = 1.5
_B = 0.75


def search_full_text(
	records: list[MemoryRecord],
	query: NormalizedQuery,
	*,
	top_n: int,
) -> list[RetrievalMatch]:
	if top_n < 1 or not query.tokens:
		return []

	tokenized_records: list[tuple[int, MemoryRecord, list[str]]] = []
	for index, record in enumerate(records):
		tokens = list(normalize_query(_document_text(record)).tokens)
		if tokens:
			tokenized_records.append((index, record, tokens))

	if not tokenized_records:
		return []

	document_frequency: Counter[str] = Counter()
	document_lengths: dict[str, int] = {}
	term_frequencies: dict[str, Counter[str]] = {}

	for _index, record, tokens in tokenized_records:
		term_counter = Counter(tokens)
		term_frequencies[record.memory_id] = term_counter
		document_lengths[record.memory_id] = len(tokens)
		document_frequency.update(term_counter.keys())

	average_length = sum(document_lengths.values()) / len(document_lengths)
	unique_terms = tuple(dict.fromkeys(query.tokens))

	scored_matches: list[tuple[int, RetrievalMatch]] = []
	for index, record, _tokens in tokenized_records:
		score = 0.0
		term_counter = term_frequencies[record.memory_id]
		document_length = document_lengths[record.memory_id]
		for term in unique_terms:
			frequency = term_counter.get(term, 0)
			if frequency == 0:
				continue
			doc_freq = document_frequency.get(term, 0)
			inverse_document_frequency = log(
				1.0 + ((len(tokenized_records) - doc_freq + 0.5) / (doc_freq + 0.5))
			)
			denominator = frequency + _K1 * (1.0 - _B + _B * (document_length / average_length))
			score += inverse_document_frequency * ((frequency * (_K1 + 1.0)) / denominator)

		if score > 0.0:
			scored_matches.append(
				(index, RetrievalMatch(memory=record, score=score, source="full_text"))
			)

	scored_matches.sort(key=lambda item: (-item[1].score, item[0]))
	return [match for _index, match in scored_matches[:top_n]]


def _document_text(record: MemoryRecord) -> str:
	parts: list[str] = []
	if record.title:
		parts.append(record.title)
	if record.summary:
		parts.append(record.summary)
	parts.append(record.text)
	if record.tags:
		parts.append(" ".join(record.tags))
	if record.heading_path:
		parts.append(" ".join(record.heading_path))
	return "\n".join(parts)
