# Performance Tuning Guide

## Retrieval bounds

Tune these settings first when search latency or memory pressure grows:

- `retrieval.vector_top_n`
- `retrieval.fts_top_n`
- `retrieval.vector_candidate_multiplier`
- `retrieval.fts_candidate_multiplier`
- `retrieval.final_top_k`
- `reranker.top_n`

Keep candidate sets bounded before graph expansion and reranking.

## Embedding and reranking throughput

- raise `embeddings.batch_size` only when model latency and memory use remain acceptable;
- reuse provider runtimes instead of per-request construction;
- set remote concurrency and connection pool limits conservatively.

## Ingestion throughput

Use these controls to limit transaction size and memory spikes during bulk ingestion:

- `ingestion.max_chunks_per_document_batch`
- `ingestion.max_chunks_per_transaction`
- `chunking.max_tokens`
- `chunking.overlap_tokens`

## Observability costs

In production, keep retrieval debug payloads disabled unless you are diagnosing a live issue.

```yaml
application:
  environment: production
retrieval:
  include_component_scores: false
  include_debug: false
```

## Verification loop

1. Measure a baseline.
2. Change one bound at a time.
3. Re-run representative searches and evals.
4. Keep Recall@K, MRR, and nDCG within the approved range.