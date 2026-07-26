"""Optional reranking for the bounded candidate set."""

from __future__ import annotations

from time import perf_counter

from .types import RetrievalCandidate

with_reranker = True

try:
	from fastembed.rerank.cross_encoder import TextCrossEncoder
except Exception:  # pragma: no cover - optional dependency surface
	TextCrossEncoder = None  # type: ignore[assignment]
	with_reranker = False


def rerank_candidates(
	candidates: list[RetrievalCandidate],
	*,
	query_text: str,
	enabled: bool,
	model: str,
	top_n: int,
) -> list[RetrievalCandidate]:
	for candidate in candidates:
		candidate.debug.setdefault("rerank_applied", False)

	if not enabled or top_n < 1 or not candidates or TextCrossEncoder is None:
		return candidates

	bounded = candidates[:top_n]
	started = perf_counter()
	try:
		encoder = TextCrossEncoder(model)
		documents = [candidate.memory.text for candidate in bounded]
		scores = [
			float(score)
			for score in encoder.rerank(query_text, documents, batch_size=min(32, len(documents)))
		]
	except Exception as exc:  # pragma: no cover - runtime fallback
		duration_ms = (perf_counter() - started) * 1000.0
		for candidate in candidates:
			candidate.debug["rerank_error"] = str(exc)
			candidate.debug["rerank_duration_ms"] = duration_ms
		return candidates

	duration_ms = (perf_counter() - started) * 1000.0

	for candidate, score in zip(bounded, scores, strict=False):
		candidate.component_scores["reranker"] = score
		candidate.debug["rerank_applied"] = True
		candidate.debug["rerank_duration_ms"] = duration_ms
		candidate.debug["rerank_model"] = model
		candidate.debug["rerank_input_size"] = len(documents)

	return candidates
