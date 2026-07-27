"""Focused application service for retrieval and chunk lookup flows."""

from __future__ import annotations

from typing import Any

from ..errors import MemoryNotFoundError, MemoryStoreError, RetrievalError
from ..models import (
    ChunkContextResponse,
    ChunkSearchResult,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryType,
    Scope,
)
from ..retrieval import (
    build_hard_filter,
    deduplicate_candidates,
    expand_one_hop_candidates,
    filter_records,
    normalize_query,
    reciprocal_rank_fuse,
    rerank_candidates,
    score_vector_candidates,
    search_full_text,
)
from ..scoring import enrich_candidate_scores, normalize_candidate_scores, score_candidates


class RetrievalService:
    """Owns search, chunk lookup, and chunk context behavior."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        repository = self._owner._repository()
        try:
            normalized_query = normalize_query(query)
            hard_filter = build_hard_filter(query)
            query_vector = self._owner._embed_search_query(normalized_query.text)
            vector_candidates = repository.search_vector_candidates(
                hard_filter=hard_filter,
                query_vector=query_vector,
                top_n=self._owner.settings.retrieval.vector_top_n,
                oversample=self._owner.settings.retrieval.vector_candidate_multiplier,
            )
            full_text_candidates = repository.search_full_text_candidates(
                hard_filter=hard_filter,
                query=normalized_query,
                top_n=self._owner.settings.retrieval.fts_top_n,
                oversample=self._owner.settings.retrieval.fts_candidate_multiplier,
            )
            vector_candidates = filter_records(vector_candidates, query)
            full_text_candidates = filter_records(full_text_candidates, query)
            if not vector_candidates and not full_text_candidates:
                return []

            vector_matches = score_vector_candidates(
                vector_candidates,
                query_vector,
                top_n=self._owner.settings.retrieval.vector_top_n,
            )
            full_text_matches = search_full_text(
                full_text_candidates,
                normalized_query,
                top_n=self._owner.settings.retrieval.fts_top_n,
            )

            candidates = reciprocal_rank_fuse(
                vector_matches=vector_matches,
                full_text_matches=full_text_matches,
                rrf_k=self._owner.settings.retrieval.rrf_k,
            )
            if not candidates:
                return []

            candidates = deduplicate_candidates(candidates)

            if (
                self._owner.settings.retrieval.graph_expansion_enabled
                and self._owner.settings.retrieval.graph_expansion_hops > 0
            ):
                candidates = expand_one_hop_candidates(
                    candidates,
                    hard_filter=hard_filter,
                    read_links=lambda memory_id: repository.read_one_hop_links(memory_id),
                )
                candidates = deduplicate_candidates(candidates)

            enrich_candidate_scores(
                candidates,
                temporal_settings=self._owner.settings.scoring.temporal,
                now=self._owner._utcnow(),
            )
            weight_map = self._owner._scoring_weights()
            normalize_candidate_scores(candidates)
            score_candidates(
                candidates,
                weight_map,
                reranker_enabled=self._owner.settings.reranker.enabled,
            )
            candidates.sort(
                key=lambda candidate: (
                    -candidate.final_score,
                    -candidate.component_scores.get("retrieval_fusion", 0.0),
                    candidate.memory.memory_id,
                )
            )

            rerank_candidates(
                candidates,
                query_text=normalized_query.text,
                enabled=self._owner.settings.reranker.enabled,
                model=self._owner.settings.reranker.model,
                top_n=min(self._owner.settings.reranker.top_n, len(candidates)),
                provider=self._owner._reranker_provider(),
            )

            normalize_candidate_scores(candidates)
            score_candidates(
                candidates,
                weight_map,
                reranker_enabled=self._owner.settings.reranker.enabled,
            )
            candidates.sort(
                key=lambda candidate: (
                    -candidate.final_score,
                    -candidate.component_scores.get("retrieval_fusion", 0.0),
                    candidate.memory.memory_id,
                )
            )

            limit = min(query.limit, self._owner.settings.retrieval.final_top_k)
            return [
                candidate.to_result(
                    include_component_scores=self._owner.settings.retrieval.include_component_scores,
                    include_debug=self._owner.settings.retrieval.include_debug,
                )
                for candidate in candidates[:limit]
            ]
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise RetrievalError(f"Search failed: {exc}") from exc

    def search_document_chunks(
        self,
        *,
        text: str,
        scope: Scope,
        limit: int = 10,
        before: int = 0,
        after: int = 0,
        include_removed: bool = False,
        allow_retrieval_only: bool = True,
    ) -> list[ChunkSearchResult]:
        del before, after
        results = self.search(
            MemorySearchQuery(
                scope=scope,
                text=text,
                limit=limit,
                memory_types=[MemoryType.DOCUMENT_CHUNK],
                include_removed=include_removed,
                allow_retrieval_only=allow_retrieval_only,
            )
        )
        return [ChunkSearchResult.from_search_result(result) for result in results]

    def get_chunk(self, chunk_id: str, *, scope: Scope | None = None) -> ChunkSearchResult | None:
        record = self._owner._repository().get_chunk_by_id(chunk_id, scope=scope)
        if record is None:
            return None
        return ChunkSearchResult.from_record(record, debug={"lookup": "chunk_id"})

    def get_chunk_context(
        self,
        chunk_id: str,
        *,
        scope: Scope | None = None,
        before: int = 0,
        after: int = 0,
    ) -> ChunkContextResponse:
        record = self._owner._repository().get_chunk_by_id(chunk_id, scope=scope)
        if record is None:
            raise MemoryNotFoundError(f"Document chunk was not found: {chunk_id}")
        if record.source_path is None or record.document_chunk_index is None:
            raise RetrievalError(
                "Document chunk context requires source_path and document_chunk_index."
            )

        resolved_scope = scope or record.scope
        window = self._owner._repository().list_chunk_window(
            record.source_path,
            scope=resolved_scope,
            document_chunk_index=record.document_chunk_index,
            before=before,
            after=after,
        )

        target = ChunkSearchResult.from_record(record, debug={"lookup": "chunk_context"})
        before_chunks: list[ChunkSearchResult] = []
        after_chunks: list[ChunkSearchResult] = []

        for window_record in window:
            chunk = ChunkSearchResult.from_record(
                window_record,
                debug={"lookup": "chunk_context"},
            )
            if window_record.memory_id == record.memory_id:
                target = chunk
                continue
            if (window_record.document_chunk_index or -1) < record.document_chunk_index:
                before_chunks.append(chunk)
                continue
            after_chunks.append(chunk)

        return ChunkContextResponse(
            chunk=target,
            before=before_chunks,
            after=after_chunks,
        )