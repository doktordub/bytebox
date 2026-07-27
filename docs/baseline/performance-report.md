# ByteBox Phase 0 Performance Report

Snapshot date: 2026-07-26

## Environment

- OS: Windows 11 10.0.26200, AMD64
- Python: CPython 3.13.12
- `arcadedb-embedded`: 26.7.2
- `fastembed`: 0.8.0
- Database mode: temporary local ArcadeDB Embedded path
- Search settings for the runtime sample: reranker disabled, graph expansion disabled, `final_top_k = 5`

## Method

Two baseline measurements were taken.

1. Real FastEmbed first-use timing with the configured default embedding model.
2. A temporary-database end-to-end script measuring startup, CRUD, search, ingest, chunk search, and shutdown using the current `MemoryStore` facade.

These are single-sample workstation measurements, not CI-grade benchmarks. They are sufficient for Phase 0 baseline capture and for setting initial review targets.

## Measured Results

### Model initialization

| Operation | Result |
|---|---:|
| First real embedding request (`FastEmbedProvider().embed_text("hello world")`) | 6387.84 ms |
| Returned embedding dimension | 384 |
| Resolved model identity | `BAAI/bge-small-en-v1.5` / `qdrant/bge-small-en-v1.5-onnx-q` |

Observed behavior during first model initialization:

- FastEmbed performed a live Hugging Face download at runtime.
- The runtime emitted an unauthenticated Hugging Face warning.
- Windows emitted a symlink privilege warning for the cache path under `%TEMP%\\fastembed_cache`.
- FastEmbed logged an error about `[WinError 1314]` and fell back to another source before succeeding.

This confirms the current implementation is not production-safe for reproducible offline startup.

### End-to-end sample

The following sample used a fresh temporary database and then performed memory CRUD, one search, one document ingest for `docs/architecture.md`, one chunk search, and shutdown.

| Operation | Result |
|---|---:|
| Store startup | 1139.01 ms |
| Create memory with embedding | 235.04 ms |
| Get memory | 34.45 ms |
| Update memory | 8.95 ms |
| Search memories | 11.30 ms |
| Ingest `docs/architecture.md` | 4387.51 ms |
| Chunk-search ingested document | 63.04 ms |
| Store shutdown | 254.92 ms |

Result details from the ingest sample:

- Added chunks: `81`
- Updated chunks: `0`
- Removed chunks: `0`
- Unchanged chunks: `0`

## Performance Interpretation

- Current startup is dominated by database opening and schema/index initialization.
- Warm create/search paths are locally acceptable at low scale, but they are not yet representative of larger corpora because the current retrieval design still materializes scoped records in Python.
- Document ingestion is already a multi-second operation for a single architecture document, which is acceptable for a local baseline but establishes clear pressure to add provider reuse, batching, and bounded candidate selection later.
- The current model initialization path is the largest operational concern because it can trigger a live model download and Windows-specific filesystem privilege issues.

## Provisional Targets For Review

These are initial review targets derived from measured behavior. They are intentionally conservative and should be replaced by multi-run benchmark thresholds once a dedicated benchmark harness exists.

| Capability | Measured baseline | Initial target for review |
|---|---:|---:|
| Warm store startup | 1139.01 ms | <= 2500 ms |
| Warm create with embedding | 235.04 ms | <= 500 ms |
| Warm point read | 34.45 ms | <= 100 ms |
| Warm update | 8.95 ms | <= 50 ms |
| Warm search on small local corpus | 11.30 ms | <= 100 ms |
| Single-document ingest (`docs/architecture.md`, 81 chunks) | 4387.51 ms | <= 5500 ms |
| Warm shutdown | 254.92 ms | <= 1000 ms |

Provider initialization target is intentionally not expressed as a simple latency bound yet. The measured cold path proves the larger requirement:

- production startup must not download model artifacts at runtime;
- model artifacts must be locally provisioned and integrity-checked before startup;
- a warm, locally provisioned first embedding call should become the future latency baseline once offline provisioning exists.

## Gate Result

Phase 0 performance baseline capture is complete.

- Representative local timings exist for startup, CRUD, search, ingestion, and shutdown.
- The current model-init path is measured and documented as a blocking production concern rather than an assumed future fix.