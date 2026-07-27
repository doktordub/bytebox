"""Optional reranking for the bounded candidate set."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from .types import RetrievalCandidate

try:  # pragma: no cover - exercised through provider integration tests
	from fastembed.rerank.cross_encoder import TextCrossEncoder as TextCrossEncoder
except Exception:  # pragma: no cover - optional dependency failure path
	TextCrossEncoder = None


def rerank_candidates(
	candidates: list[RetrievalCandidate],
	*,
	query_text: str,
	enabled: bool,
	model: str,
	top_n: int,
	provider: Any | None,
) -> list[RetrievalCandidate]:
	for candidate in candidates:
		candidate.debug.setdefault("rerank_applied", False)

	if not enabled or top_n < 1 or not candidates or provider is None:
		return candidates

	bounded = candidates[:top_n]
	documents = [candidate.memory.text for candidate in bounded]
	started = perf_counter()
	try:
		scores = provider.rerank(query_text, documents, top_n=len(documents))
		identity = provider.identity()
	except Exception as exc:  # pragma: no cover - runtime fallback
		duration_ms = (perf_counter() - started) * 1000.0
		safe_code = getattr(exc, "code", exc.__class__.__name__)
		for candidate in candidates:
			candidate.debug["rerank_error"] = safe_code
			candidate.debug["rerank_duration_ms"] = duration_ms
		return candidates

	duration_ms = (perf_counter() - started) * 1000.0
	rerank_model = getattr(identity, "model_name", model)

	for candidate, score in zip(bounded, scores, strict=False):
		candidate.component_scores["reranker"] = score
		candidate.debug["rerank_applied"] = True
		candidate.debug["rerank_duration_ms"] = duration_ms
		candidate.debug["rerank_model"] = rerank_model
		candidate.debug["rerank_input_size"] = len(documents)

	return candidates
