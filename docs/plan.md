# Local-First Agent Memory Store — Phased Implementation Plan

**Document status:** Implementation plan  
**Generated:** 2026-06-13  
**Inputs reviewed:** `prompt.md`, `architecture.md`  
**Target system:** Lightweight, portable, local-first Python memory store for AI agents  
**Primary implementation style:** Embedded/single-process first, Python-first API, thin REST/CLI adapters

---

## 0. Purpose

This plan converts `architecture.md` into an actionable build sequence for implementing the local-first agent memory store. It is organized into phases with:

- clear implementation goals
- concrete deliverables
- test deliverables
- acceptance gates
- dependencies
- references back to `architecture.md`

The plan assumes V1 must remain lightweight, local-first, deterministic, and testable. The implementation should prioritize correctness, safety, lifecycle clarity, and retrieval explainability before advanced optimization.

---

## 1. Architecture References Used

| Plan Area | Primary Architecture Reference |
|---|---|
| V1 principles and constraints | `architecture.md §3`, `architecture.md §22`, `architecture.md §25` |
| Layered architecture and module boundaries | `architecture.md §5`, `architecture.md §6` |
| Python-first public API | `architecture.md §7` |
| Agent memory vs document chunk lifecycle separation | `architecture.md §8` |
| Core data model | `architecture.md §9` |
| ArcadeDB schema and persistence design | `architecture.md §10` |
| Markdown ingestion | `architecture.md §11` |
| Hybrid retrieval | `architecture.md §12` |
| Configurable scoring | `architecture.md §13` |
| Privacy and data controls | `architecture.md §14` |
| Operational safety | `architecture.md §15` |
| REST wrapper | `architecture.md §16` |
| CLI | `architecture.md §17` |
| Configuration | `architecture.md §18` |
| Evaluation suite | `architecture.md §19` |
| Testing architecture | `architecture.md §20` |
| Implementation milestones | `architecture.md §21` |
| Risks and mitigations | `architecture.md §23` |
| Acceptance checklist | `architecture.md §24` |

---

## 2. Guiding Implementation Rules

These rules should be treated as hard constraints throughout the implementation.

1. **Keep `MemoryStore` as the primary public API.**  
   REST and CLI must be adapters, not separate business-logic paths.  
   Reference: `architecture.md §3`, `architecture.md §5.1`, `architecture.md §7`.

2. **Centralize orchestration in `MemoryService`.**  
   `store.py`, `api/`, and `cli.py` should all call the same service layer.  
   Reference: `architecture.md §5.2`, `architecture.md §6.1`.

3. **Keep ArcadeDB details isolated in `arcade/`.**  
   Service code should call repository functions, not embed raw ArcadeDB query logic everywhere.  
   Reference: `architecture.md §5.5`, `architecture.md §10`.

4. **Do not mix agent memory lifecycle with document chunk lifecycle.**  
   Agent memories are lifecycle-managed; document chunks are source-controlled and updated through deterministic re-ingestion.  
   Reference: `architecture.md §8`.

5. **Make retrieval explainable.**  
   Every search result must include final score, raw component scores, normalized component scores, and debug metadata.  
   Reference: `architecture.md §12`, `architecture.md §13`.

6. **Implement operational safety before optimization.**  
   Schema versioning, migrations, embedding dimension validation, privacy filters, and deterministic ingestion must be reliable before tuning retrieval performance.  
   Reference: `architecture.md §15`, `architecture.md §23`.

---

## 3. Phase Overview

| Phase | Name | Primary Outcome | Depends On |
|---:|---|---|---|
| 0 | [DONE] Project Setup and Build Guardrails | Repo/package can install, lint, type-check, and run tests | None |
| 1 | [DONE] Configuration and Domain Models | Typed config and Pydantic domain model foundation | Phase 0 |
| 2 | [DONE] ArcadeDB Embedded Persistence Foundation | Local DB opens, schema initializes, migrations validate | Phase 1 |
| 3 | [DONE] Core Service Layer and `MemoryStore` API | Add/get/update/upsert memory through Python API | Phase 2 |
| 4 | [DONE] Embeddings and Operational Safety | FastEmbed provider and dimension/model metadata validation | Phase 3 |
| 5 | [DONE] Agent Memory Lifecycle | Promote, supersede, contradict, expire, forget, feedback | Phase 4 |
| 6 | [DONE] Deterministic Markdown Ingestion | Stable chunk IDs, skip/update/insert/remove re-ingestion behavior | Phase 4 |
| 7 | [DONE] Hybrid Retrieval Pipeline | Vector + FTS + RRF + dedupe + optional one-hop graph expansion | Phases 5–6 |
| 8 | [DONE] Scoring and Diagnostics | Configurable final scoring with normalized/raw component scores | Phase 7 |
| 9 | [DONE] Privacy, Import/Export, and Redaction | Scope-level controls and retrieval exclusion rules | Phase 8 |
| 10 | [DONE] REST API and CLI Adapters | REST and CLI call shared service layer | Phase 9 |
| 11 | [DONE] Evaluation Suite, Documentation, and Release Hardening | Golden query evals, README, examples, release checklist | Phase 10 |

---

## 4. [DONE] Phase 0 — Project Setup and Build Guardrails

### Goal

Create the minimal package skeleton and local development workflow so all later phases are testable and repeatable.

### Architecture References

- `architecture.md §3` — V1 architectural principles
- `architecture.md §5` — layered architecture
- `architecture.md §6` — package structure
- `architecture.md §20` — testing architecture
- `architecture.md §21` — implementation milestones

### Implementation Deliverables

- [DONE] `memory_store/` package directory
- [DONE] subpackages:
  - [DONE] `arcade/`
  - [DONE] `api/`
  - [DONE] `embeddings/`
  - [DONE] `evals/`
  - [DONE] `ingestion/`
  - [DONE] `retrieval/`
  - [DONE] `tests/`
  - [DONE] `examples/`
- [DONE] top-level modules:
  - [DONE] `__init__.py`
  - [DONE] `config.py`
  - [DONE] `errors.py`
  - [DONE] `models.py`
  - [DONE] `store.py`
  - [DONE] `service.py`
  - [DONE] `scoring.py`
  - [DONE] `lifecycle.py`
  - [DONE] `privacy.py`
  - [DONE] `cli.py`
- [DONE] `pyproject.toml`
- [DONE] baseline `README.md`
- [DONE] baseline `config.example.yaml`
- [DONE] `.gitignore`
- [DONE] task runner commands for common workflows documented in `README.md` and project script entry points

### Recommended Dependencies

Start with only the required/lightweight dependencies:

- Python 3.12+
- `arcadedb-embedded`
- `fastembed`
- `fastapi`
- `pydantic` v2
- `pyyaml`
- `pytest`
- `ruff`
- `mypy`
- `uvicorn` for REST dev runs

### Test Deliverables

- [DONE] `tests/test_imports.py`
- [DONE] smoke test confirming:
  - [DONE] package imports
  - [DONE] basic modules import
  - [DONE] `pytest` runs
  - [DONE] `ruff` runs
  - [DONE] `mypy` baseline can be introduced without blocking early work

### Acceptance Gate

- [DONE] `python -m pytest` runs.
- [DONE] `python -c "import memory_store"` succeeds.
- [DONE] Package structure matches `architecture.md §6`.
- [DONE] No business logic is placed in REST or CLI adapters.

---

## 5. [DONE] Phase 1 — Configuration and Domain Models

### Goal

Build the typed domain foundation before persistence and retrieval. This keeps all later modules aligned around the same validated models.

### Architecture References

- `architecture.md §5.3` — domain model layer
- `architecture.md §7` — public API architecture
- `architecture.md §9` — core data model architecture
- `architecture.md §18` — configuration architecture

### Implementation Deliverables

#### [DONE] `config.py`

- [DONE] `MemoryStoreSettings`
- [DONE] nested settings models:
  - [DONE] `DatabaseSettings`
  - [DONE] `EmbeddingSettings`
  - [DONE] `RerankerSettings`
  - [DONE] `RetrievalSettings`
  - [DONE] `ScoringSettings`
  - [DONE] `ChunkingSettings`
  - [DONE] `PrivacySettings`
  - [DONE] `ApiSettings`
  - [DONE] `LoggingSettings`
- [DONE] config loader supporting precedence:

```text
defaults < config.yaml < environment variables < explicit code arguments
```

#### [DONE] `models.py`

Implement Pydantic v2 models and enums:

- [DONE] `Scope`
- [DONE] `MemoryType`
- [DONE] `MemoryStatus`
- [DONE] `SensitivityLevel`
- [DONE] `SourceType`
- [DONE] `MemoryCreate`
- [DONE] `MemoryUpdate`
- [DONE] `MemoryRecord`
- [DONE] `MemorySearchQuery`
- [DONE] `MemorySearchResult`
- [DONE] `MemoryFeedback`
- [DONE] `IngestResult`
- [DONE] `FolderIngestResult`
- [DONE] `MemoryExport`
- [DONE] `MemoryImport`
- [DONE] `ImportResult`
- [DONE] `RedactionResult`
- [DONE] `MemoryStats`
- [DONE] `HealthStatus`

#### [DONE] `errors.py`

Explicit typed exceptions:

- [DONE] `MemoryStoreError`
- [DONE] `ConfigError`
- [DONE] `ValidationError`
- [DONE] `MemoryNotFoundError`
- [DONE] `SchemaMismatchError`
- [DONE] `EmbeddingDimensionMismatchError`
- [DONE] `PersistenceError`
- [DONE] `PrivacyError`
- [DONE] `LifecycleError`
- [DONE] `IngestionError`
- [DONE] `RetrievalError`

### Test Deliverables

- [DONE] config default loading
- [DONE] config YAML override
- [DONE] environment variable override
- [DONE] explicit code override
- [DONE] model validation for required fields
- [DONE] enum validation
- [DONE] score field range validation
- [DONE] `MemorySearchResult` supports raw and normalized scores

### Acceptance Gate

- [DONE] Config precedence works exactly as defined.
- [DONE] All public API inputs/outputs have typed Pydantic models.
- [DONE] `MemoryRecord` includes identity, scope, classification, content, source, embedding, scoring, temporal, privacy, and lifecycle fields.
- [DONE] Search result model includes `final_score`, `component_scores`, `normalized_scores`, and `debug`.

---

## 6. [DONE] Phase 2 — ArcadeDB Embedded Persistence Foundation

### Goal

Create a deterministic local database foundation with schema versioning, migration tracking, metadata indexes, graph edge types, and repository operations.

### Architecture References

- `architecture.md §5.5` — persistence layer
- `architecture.md §10` — ArcadeDB data architecture
- `architecture.md §15.2` — schema safety
- `architecture.md §21` — Milestone 2
- `architecture.md §24` — package and database acceptance checklist

### Implementation Deliverables

#### [DONE] `arcade/connection.py`

- [DONE] embedded database open/create logic
- [DONE] database path handling
- [DONE] single-process guardrails
- [DONE] startup/shutdown hooks where useful

#### [DONE] `arcade/schema.py`

Create deterministic schema for:

- [DONE] `MemoryRecord` vertex
- [DONE] `SchemaVersion` vertex
- [DONE] `MigrationRecord` vertex
- [DONE] optional `RedactionAudit` vertex
- [DONE] optional `ImportExportAudit` vertex

Create edge types:

- [DONE] `SUPERSEDES`
- [DONE] `CONTRADICTS`
- [DONE] `DERIVED_FROM`
- [DONE] `RELATED_TO`
- [DONE] `SUPPORTS`
- [DONE] `MENTIONS`

Create indexes:

- [DONE] vector index on `embedding`
- [DONE] full-text indexes on:
  - [DONE] `title`
  - [DONE] `summary`
  - [DONE] `text`
  - [DONE] `tags` via derived `tags_text`
  - [DONE] `source_path`
- [DONE] metadata indexes on:
  - [DONE] `memory_type`
  - [DONE] `project_id`
  - [DONE] `user_id`
  - [DONE] `agent_id`
  - [DONE] `status`
  - [DONE] `created_at`
  - [DONE] `stable_key`
  - [DONE] `source_hash`
  - [DONE] `chunk_id`

#### [DONE] `arcade/migrations.py`

- [DONE] monotonic schema version model
- [DONE] migration registration
- [DONE] idempotent migration execution where practical
- [DONE] startup schema compatibility validation

#### [DONE] `arcade/queries.py`

Repository methods:

- [DONE] insert memory
- [DONE] get memory by ID
- [DONE] update memory
- [DONE] upsert by stable key
- [DONE] query by scope
- [DONE] query by chunk ID/source hash
- [DONE] create edge
- [DONE] read one-hop neighbors
- [DONE] mark status
- [DONE] count/stat helpers

#### [DONE] `arcade/transactions.py`

- [DONE] transaction context manager
- [DONE] batch insert/update helpers
- [DONE] rollback behavior for failed ingestion/lifecycle operations

### Test Deliverables

- [DONE] database initializes locally
- [DONE] schema creates deterministically
- [DONE] schema version is written
- [DONE] schema version validates at startup
- [DONE] migrations are idempotent
- [DONE] required vertex and edge types exist
- [DONE] required indexes exist
- [DONE] insert/get/update memory repository tests
- [DONE] transaction rollback test

### Acceptance Gate

- [DONE] ArcadeDB Embedded initializes from Python.
- [DONE] Schema is deterministic.
- [DONE] Schema version is validated at startup.
- [DONE] Migrations run idempotently.
- [DONE] Repository methods do not leak ArcadeDB query details into service code.

---

## 7. [DONE] Phase 3 — Core Service Layer and `MemoryStore` API

### Goal

Implement the Python-first public API and shared service layer for core memory operations.

### Architecture References

- `architecture.md §5.1` — adapter layer
- `architecture.md §5.2` — application service layer
- `architecture.md §6.1` — boundary rule
- `architecture.md §7` — public API architecture
- `architecture.md §21` — Milestone 3

### Implementation Deliverables

#### [DONE] `service.py`

Implement `MemoryService` methods:

- [DONE] `add_memory`
- [DONE] `get_memory`
- [DONE] `update_memory`
- [DONE] `upsert_memory`
- [DONE] `stats`
- [DONE] `health`

At this phase, `search` may be a placeholder or simple metadata/text lookup until full retrieval is implemented in Phase 7.

#### [DONE] `store.py`

Implement `MemoryStore` facade:

- [DONE] `from_config`
- [DONE] `add_memory`
- [DONE] `get_memory`
- [DONE] `update_memory`
- [DONE] `upsert_memory`
- [DONE] placeholder pass-through methods for later phases

Rules:

- [DONE] `MemoryStore` should not contain business logic.
- [DONE] `MemoryStore` should delegate to `MemoryService`.
- [DONE] `MemoryService` should delegate persistence to `arcade/`.

#### [DONE] `__init__.py`

Export common public types:

```python
from memory_store.store import MemoryStore
from memory_store.models import MemoryCreate, MemoryRecord, MemorySearchQuery, Scope
```

### Test Deliverables

- [DONE] `MemoryStore.from_config` initializes service and database
- [DONE] `add_memory` persists a record
- [DONE] `get_memory` retrieves a record
- [DONE] `update_memory` increments version and updates timestamp
- [DONE] `upsert_memory` respects stable key
- [DONE] `health` returns database/schema status
- [DONE] `stats` returns basic counts

### Acceptance Gate

- [DONE] A memory can be added and retrieved through `MemoryStore`.
- [DONE] Stable-key upsert works.
- [DONE] Python API calls service layer only.
- [DONE] No REST or CLI business logic is required yet.

---

## 8. [DONE] Phase 4 — Embeddings and Operational Safety

### Goal

Add FastEmbed embeddings and enforce embedding safety before vector retrieval is implemented.

### Architecture References

- `architecture.md §5.4` — capability modules
- `architecture.md §9.1` — embedding field group
- `architecture.md §10.4` — vector index design
- `architecture.md §13` — scoring architecture dependencies
- `architecture.md §15.1` — embedding safety
- `architecture.md §21` — Milestone 4

### Implementation Deliverables

#### [DONE] `embeddings/fastembed_provider.py`

- [DONE] local embedding provider interface
- [DONE] FastEmbed implementation
- [DONE] batch embedding support
- [DONE] embedding normalization option
- [DONE] model name/version metadata collection where available

#### [DONE] `embeddings/validation.py`

- [DONE] validate embedding dimension
- [DONE] validate active index dimension
- [DONE] mismatch policy handling:
  - [DONE] `error`
  - [DONE] `quarantine`
  - [DONE] `reembed`
- [DONE] reusable validation function before insert/update

#### [DONE] Searchable Text Builder

Implement a shared function for converting memory/chunk content into embedding text. It should support:

- [DONE] title
- [DONE] summary
- [DONE] tags
- [DONE] source metadata
- [DONE] heading path
- [DONE] body text

### Persistence Updates

- [DONE] store `embedding`
- [DONE] store `embedding_model`
- [DONE] store `embedding_model_version`
- [DONE] store `embedding_dim`
- [DONE] store `embedding_created_at`

### Test Deliverables

- [DONE] FastEmbed provider can embed sample text
- [DONE] embedding dimensions match config
- [DONE] dimension mismatch raises or quarantines according to config
- [DONE] model metadata is stored
- [DONE] embedding created timestamp is stored
- [DONE] batch embedding returns deterministic count/order

### Acceptance Gate

- [DONE] Invalid embedding dimensions are rejected or handled by policy.
- [DONE] Embedded records store model, version/revision, dimension, and timestamp.
- [DONE] `add_memory` can optionally embed content before insert.
- [DONE] Safety errors are explicit and test-covered.

---

## 9. [DONE] Phase 5 — Agent Memory Lifecycle

### Goal

Implement lifecycle controls for mutable agent memory records while keeping document chunk lifecycle separate.

### Architecture References

- `architecture.md §8.1` — agent memory lifecycle
- `architecture.md §9.2` — status model
- `architecture.md §10.3` — graph edge types
- `architecture.md §14` — privacy interaction
- `architecture.md §21` — Milestone 8

### Implementation Deliverables

#### [DONE] `lifecycle.py`

Implement operations:

- [DONE] `promote`
- [DONE] `supersede`
- [DONE] `contradict`
- [DONE] `expire`
- [DONE] `forget`
- [DONE] `add_feedback`

#### Lifecycle Rules

- [DONE] `promote`:
  - [DONE] candidate/observation can become active
  - [DONE] confidence/importance may increase
  - [DONE] reason stored in metadata/audit
- [DONE] `supersede`:
  - [DONE] old record status becomes `superseded`
  - [DONE] new record remains/becomes `active`
  - [DONE] `SUPERSEDES` edge is created
  - [DONE] old record `superseded_by` is set
- [DONE] `contradict`:
  - [DONE] both records remain stored
  - [DONE] `CONTRADICTS` edge is created
  - [DONE] confidence can be lowered or conflict metadata stored
- [DONE] `expire`:
  - [DONE] status becomes `expired`
  - [DONE] retrieval excludes it by default
- [DONE] `forget`:
  - [DONE] status becomes `forgotten` or hard delete according to privacy policy
  - [DONE] retrieval excludes it
- [DONE] `add_feedback`:
  - [DONE] updates `confidence`, `importance`, or `user_rating`
  - [DONE] records feedback metadata

### Test Deliverables

- [DONE] promote candidate memory
- [DONE] supersede old with new memory
- [DONE] contradict two records
- [DONE] expire active memory
- [DONE] forget memory
- [DONE] feedback updates score inputs
- [DONE] lifecycle metadata/audit is stored
- [DONE] graph edges are created correctly
- [DONE] document chunks cannot accidentally use normal agent memory lifecycle unless explicitly allowed

### Acceptance Gate

- [DONE] Agent memories can move through all required lifecycle states.
- [DONE] Lifecycle changes are auditable.
- [DONE] Supersession/contradiction edges are created.
- [DONE] Forgotten and expired records are excluded from normal retrieval filters.

---

## 10. [DONE] Phase 6 — Deterministic Markdown Ingestion

### Goal

Implement source-controlled document chunk ingestion with stable hashes, deterministic chunk IDs, and correct re-ingestion behavior.

### Architecture References

- `architecture.md §8.2` — document chunk lifecycle
- `architecture.md §11` — markdown ingestion architecture
- `architecture.md §15.3` — transaction safety
- `architecture.md §20.2` — required determinism tests
- `architecture.md §21` — Milestone 5

### Implementation Deliverables

#### [DONE] `ingestion/frontmatter.py`

- [DONE] YAML frontmatter parser
- [DONE] field validation:
  - [DONE] `name`
  - [DONE] `description`
  - [DONE] `version`
  - [DONE] `owner`
  - [DONE] `tags`
- [DONE] field length enforcement

#### [DONE] `ingestion/markdown.py`

- [DONE] Markdown file reader
- [DONE] section extraction
- [DONE] code block preservation
- [DONE] heading path extraction

#### [DONE] `ingestion/chunking.py`

- [DONE] `markdown_section` strategy
- [DONE] max token approximation
- [DONE] overlap token support
- [DONE] include heading path option
- [DONE] preserve code blocks

#### [DONE] `ingestion/hashing.py`

- [DONE] source hash
- [DONE] content hash
- [DONE] deterministic chunk ID:

```python
chunk_id = sha256(source_path + heading_path + chunk_index + content_hash)
```

#### [DONE] Service Integration

Implement:

- [DONE] `ingest_document`
- [DONE] `ingest_folder`

Required re-ingestion behavior:

| File Change | Behavior |
|---|---|
| Same content hash | Skip unchanged chunk |
| Same section, changed text | Update and re-embed |
| New section | Insert and embed |
| Removed section | Mark `removed` or hard-delete based on config |
| Renamed file with same content | Optional relink |

### Test Deliverables

- [DONE] frontmatter parsing
- [DONE] frontmatter validation
- [DONE] markdown section chunking
- [DONE] code block preservation
- [DONE] deterministic chunk IDs
- [DONE] first ingestion inserts chunks
- [DONE] second ingestion of unchanged file skips chunks
- [DONE] changed section updates and re-embeds
- [DONE] new section inserts
- [DONE] removed section is marked removed or deleted
- [DONE] folder ingestion aggregates per-file results
- [DONE] transaction rollback on failed batch ingestion

### Acceptance Gate

- [DONE] Re-ingesting the same folder twice produces stable chunk IDs.
- [DONE] Unchanged chunks are skipped.
- [DONE] Changed chunks update and re-embed.
- [DONE] Removed chunks follow configured policy.
- [DONE] Document chunks remain governed by document lifecycle, not agent memory lifecycle.

---

## 11. [DONE] Phase 7 — Hybrid Retrieval Pipeline

### Goal

Implement explainable hybrid retrieval using vector search, full-text search, Reciprocal Rank Fusion, deduplication, optional one-hop graph expansion, and optional bounded reranking.

### Architecture References

- `architecture.md §12` — retrieval architecture
- `architecture.md §10.4` — index design
- `architecture.md §13` — scoring input requirements
- `architecture.md §19` — evaluation architecture
- `architecture.md §21` — Milestone 6

### Implementation Deliverables

#### [DONE] `retrieval/filters.py`

- [DONE] query normalization
- [DONE] structured hard filter model
- [DONE] filter construction for:
  - [DONE] `user_id`
  - [DONE] `project_id`
  - [DONE] `agent_id`
  - [DONE] `memory_type`
  - [DONE] `status = active`
  - [DONE] `source_type`
  - [DONE] `sensitivity`
  - [DONE] `allow_retrieval = true`
  - [DONE] optional explicit sensitive access

#### [DONE] `retrieval/vector.py`

- [DONE] query embedding
- [DONE] vector search top N
- [DONE] cosine similarity handling
- [DONE] raw vector score capture

#### [DONE] `retrieval/full_text.py`

- [DONE] full-text/BM25 search top N
- [DONE] raw full-text score capture

#### [DONE] `retrieval/fusion.py`

- [DONE] Reciprocal Rank Fusion
- [DONE] configurable `rrf_k`
- [DONE] vector and full-text rank preservation

#### [DONE] `retrieval/graph.py`

- [DONE] optional one-hop graph expansion
- [DONE] edge type labeling
- [DONE] graph score as separate component
- [DONE] debug metadata for expanded records

#### [DONE] `retrieval/rerank.py`

- [DONE] optional FastEmbed reranker
- [DONE] bounded candidate set, default top 40–80
- [DONE] raw reranker score capture

#### [DONE] Service Integration

Implement `MemoryService.search` and `MemoryStore.search`.

Pipeline order:

1. [DONE] Normalize query.
2. [DONE] Extract structured filters.
3. [DONE] Apply hard filters.
4. [DONE] Dense vector search top N.
5. [DONE] Full-text search top N.
6. [DONE] Merge with RRF.
7. [DONE] Deduplicate by `memory_id`, `stable_key`, `chunk_id`, `source_hash`.
8. [DONE] Optional one-hop graph expansion.
9. [DONE] Optional rerank bounded candidates.
10. [DONE] Pass candidates to final scoring.
11. [DONE] Return top K with diagnostics.

### Test Deliverables

- [DONE] hard filters exclude non-active and non-retrievable records
- [DONE] vector search mock returns expected candidates
- [DONE] full-text search mock returns expected candidates
- [DONE] RRF merge behavior is correct
- [DONE] raw vector and BM25 scores are not directly mixed before fusion
- [DONE] deduplication works across IDs and chunk keys
- [DONE] graph expansion is limited to one hop
- [DONE] graph-expanded records are labeled in debug metadata
- [DONE] reranker receives bounded candidates only
- [DONE] search returns expected shape

### Acceptance Gate

- [DONE] Hybrid retrieval returns candidates from vector and full-text paths.
- [DONE] RRF is used before final scoring.
- [DONE] Graph expansion is optional and one-hop only.
- [DONE] Reranking is optional and bounded.
- [DONE] Search results include enough debug metadata for diagnosis.

---

## 12. [DONE] Phase 8 — Scoring and Diagnostics

### Goal

Implement configurable final scoring with normalized components, raw component diagnostics, per-memory-type temporal behavior, and documented reranker-disabled weight redistribution.

### Architecture References

- `architecture.md §13` — scoring architecture
- `architecture.md §12.5` — result shape
- `architecture.md §18` — configuration architecture
- `architecture.md §19` — eval diagnostics
- `architecture.md §21` — Milestone 7

### Implementation Deliverables

#### [DONE] `scoring.py`

Implement:

- [DONE] component score normalization
- [DONE] final weighted score calculation
- [DONE] default weights:
  - [DONE] `reranker`
  - [DONE] `retrieval_fusion`
  - [DONE] `vector`
  - [DONE] `full_text`
  - [DONE] `temporal`
  - [DONE] `importance`
  - [DONE] `confidence`
  - [DONE] `graph`
  - [DONE] `user_rating`
- [DONE] per-memory-type temporal decay
- [DONE] no-decay behavior for `decision` and `document_chunk`
- [DONE] missing component handling
- [DONE] reranker-disabled redistribution strategy
- [DONE] debug metadata explaining score contribution

### Recommended Reranker-Disabled Redistribution

When reranking is disabled, redistribute reranker weight to retrieval components:

| Component | Additional Weight |
|---|---:|
| `retrieval_fusion` | `+0.25` |
| `vector` | `+0.12` |
| `full_text` | `+0.08` |

This preserves retrieval-driven scoring without producing misleading zero reranker scores.

### Test Deliverables

- [DONE] every component normalizes to 0–1
- [DONE] final weights sum to 1 after redistribution
- [DONE] missing components do not produce misleading scores
- [DONE] temporal score decays by memory type
- [DONE] document chunks do not decay by default
- [DONE] decisions do not decay unless superseded
- [DONE] search result contains:
  - [DONE] final score
  - [DONE] raw component scores
  - [DONE] normalized component scores
  - [DONE] debug metadata
- [DONE] score ordering is deterministic for fixed inputs

### Acceptance Gate

- [DONE] Final scoring is configurable.
- [DONE] Raw and normalized scores are preserved.
- [DONE] Per-memory-type temporal behavior works.
- [DONE] Reranker-disabled behavior is documented and tested.
- [DONE] Scoring is inspectable and not hidden.

---

## 13. [DONE] Phase 9 — Privacy, Import/Export, and Redaction

### Goal

Implement the required privacy and data-control operations with scope-level filtering and retrieval safeguards.

### Architecture References

- `architecture.md §14` — privacy and data control architecture
- `architecture.md §9.2` — status model
- `architecture.md §15` — operational safety
- `architecture.md §23` — privacy leakage risk mitigation
- `architecture.md §24` — operational safety acceptance checklist

### Implementation Deliverables

#### `privacy.py`

Implement:

- [DONE] `forget(memory_id)`
- [DONE] `forget_by_user(user_id)`
- [DONE] `delete_by_scope(scope, hard_delete=False)`
- [DONE] `disable_memory(scope)`
- [DONE] `export_user_memories(user_id)`
- [DONE] `export_scope(scope)`
- [DONE] `import_memories(payload, mode="upsert")`
- [DONE] `redact(patterns, scope=None)`

### Required Privacy Rules

- [DONE] `forgotten` records are not returned by normal retrieval.
- [DONE] `deleted` records are not returned by normal retrieval.
- [DONE] `removed` document chunks are not returned unless `include_removed=true` is explicitly requested for audit/debug.
- [DONE] `allow_retrieval = false` excludes records from normal search.
- [DONE] `allow_llm_context = false` allows internal search but blocks use as prompt context.
- [DONE] `sensitive` records require explicit retrieval permission.
- [DONE] redaction preserves audit metadata without exposing redacted text.
- [DONE] scope-level delete/export/disable operations support `user_id`, `project_id`, and `agent_id`.

### Test Deliverables

- [DONE] forget by ID
- [DONE] forget by user
- [DONE] soft delete by scope
- [DONE] hard delete by scope, if enabled
- [DONE] disable by scope sets `allow_retrieval = false`
- [DONE] export by user
- [DONE] export by scope
- [DONE] import upsert mode
- [DONE] import conflict behavior
- [DONE] redact by regex/pattern
- [DONE] redacted text is not exposed
- [DONE] forgotten/deleted/removed records are excluded from normal retrieval
- [DONE] sensitive records require explicit permission

### Acceptance Gate

- [DONE] Required privacy methods work.
- [DONE] Retrieval filters enforce privacy rules.
- [DONE] Redaction is auditable and does not leak redacted text.
- [DONE] Export/import/delete operations are scope-aware.
- [DONE] Privacy controls are covered by deterministic tests.

---

## 14. [DONE] Phase 10 — REST API and CLI Adapters

### Goal

Add optional FastAPI and CLI adapters without duplicating service-layer business logic.

### Architecture References

- `architecture.md §5.1` — adapter layer
- `architecture.md §6.1` — boundary rule
- `architecture.md §16` — REST API architecture
- `architecture.md §17` — CLI architecture
- `architecture.md §21` — Milestone 9

### Implementation Deliverables

#### [DONE] `api/schemas.py`

[DONE] Request/response schemas for REST endpoints. These can wrap or reuse domain models.

#### [DONE] `api/routes.py`

[DONE] Required endpoints:

```http
POST   /memories
GET    /memories/{id}
PATCH  /memories/{id}
POST   /memories/search
POST   /documents/ingest
POST   /documents/ingest-folder
POST   /memories/{id}/feedback
POST   /memories/{id}/forget
POST   /memories/export
POST   /memories/import
POST   /memories/delete-by-scope
GET    /health
GET    /stats
```

#### [DONE] `api/main.py`

- [DONE] FastAPI app factory
- [DONE] config loading
- [DONE] optional local API token support
- [DONE] exception handlers

#### [DONE] `cli.py`

[DONE] Commands:

```bash
memory-store init --config config.yaml
memory-store ingest-file docs/architecture.md --project-id bb1 --user-id user-1
memory-store ingest-folder docs/ --project-id bb1 --user-id user-1
memory-store search "What reranking approach did we choose?" --project-id bb1
memory-store eval evals/golden_queries.yaml
memory-store export --user-id user-1 --out memories.json
memory-store delete-by-scope --project-id bb1 --dry-run
```

### Error Handling Deliverables

[DONE] REST errors map cleanly:

| Error | HTTP Status |
|---|---:|
| Validation error | 422 |
| Not found | 404 |
| Conflict/schema mismatch | 409 |
| Embedding mismatch | 422 or 409 |
| Privacy violation | 403 |
| Persistence error | 500 |

### Test Deliverables

- [DONE] REST route validation
- [DONE] route-to-service parity tests
- [DONE] REST handlers do not directly call ArcadeDB
- [DONE] CLI command parsing
- [DONE] CLI commands call `MemoryStore`/service
- [DONE] local API token behavior, if enabled
- [DONE] example curl requests are runnable or covered by smoke tests

### Acceptance Gate

- [DONE] REST routes call the same service layer as Python API.
- [DONE] REST route handlers do not duplicate retrieval, lifecycle, scoring, or ingestion logic.
- [DONE] CLI commands call the same Python service layer.
- [DONE] REST and CLI remain optional for V1.

---

## 15. [DONE] Phase 11 — Evaluation Suite, Documentation, and Release Hardening

### Goal

Validate the system end-to-end with golden queries, produce documentation and examples, and complete the V1 release checklist.

### Architecture References

- `architecture.md §19` — evaluation architecture
- `architecture.md §20` — testing architecture
- `architecture.md §21` — Milestone 9
- `architecture.md §23` — risks and mitigations
- `architecture.md §24` — acceptance checklist

### Implementation Deliverables

#### [DONE] `evals/golden_queries.yaml`

Include initial golden queries for:

- [DONE] reranking approach decision
- [DONE] BB1 architecture
- [DONE] LocalAI issue/debug note pattern
- [DONE] document chunk retrieval
- [DONE] lifecycle stale/superseded exclusion
- [DONE] privacy exclusion behavior

#### [DONE] `evals/runner.py`

- [DONE] load golden query fixture
- [DONE] execute searches
- [DONE] collect result diagnostics
- [DONE] measure latency
- [DONE] write JSON and Markdown outputs

#### [DONE] `evals/metrics.py`

Implement:

- [DONE] Recall@10
- [DONE] MRR
- [DONE] NDCG
- [DONE] latency p50/p95
- [DONE] reranker cost/time
- [DONE] duplicate rate
- [DONE] stale memory rate

#### [DONE] `evals/report.py`

Generate:

- [DONE] JSON summary
- [DONE] Markdown report
- [DONE] per-query details
- [DONE] component score breakdown
- [DONE] raw and normalized score diagnostics

#### [DONE] Documentation

Complete `README.md` with:

1. [DONE] what the package does
2. [DONE] when to use it
3. [DONE] when not to use it
4. [DONE] V1 boundaries and non-goals
5. [DONE] installation
6. [DONE] quickstart with Python API
7. [DONE] markdown ingestion example
8. [DONE] hybrid retrieval example
9. [DONE] REST API example
10. [DONE] CLI examples
11. [DONE] configuration reference
12. [DONE] lifecycle examples
13. [DONE] privacy/delete/export/import examples
14. [DONE] eval runner example
15. [DONE] known V1 limitations

#### [DONE] Examples

- [DONE] `examples/python_api_example.py`
- [DONE] `examples/markdown_ingest_example.py`
- [DONE] `examples/rest_example.py`
- [DONE] `examples/eval_runner_example.py`

### Test Deliverables

- [DONE] golden query fixture loads
- [DONE] eval runner outputs JSON summary
- [DONE] eval runner outputs Markdown report
- [DONE] Recall@10 works
- [DONE] MRR works
- [DONE] NDCG works
- [DONE] duplicate rate works
- [DONE] stale memory rate works
- [DONE] README examples are smoke-tested where practical
- [DONE] all acceptance checklist items are covered by tests or documented manual checks

### Acceptance Gate

- [DONE] Evals run locally.
- [DONE] Eval output includes JSON and Markdown reports.
- [DONE] Hybrid search returns expected golden memories.
- [DONE] README examples are runnable.
- [DONE] V1 acceptance checklist passes.

---

## 16. Cross-Phase Test Matrix

| Capability | Main Phase | Required Tests |
|---|---:|---|
| Package import/install | 0 | import smoke, pytest smoke |
| Config precedence | 1 | defaults, YAML, env, explicit overrides |
| Domain validation | 1 | model/enums/ranges/search result shape |
| ArcadeDB schema | 2 | create schema, indexes, edges |
| Migrations | 2 | version validation, idempotency |
| Core memory CRUD | 3 | add/get/update/upsert |
| Embedding safety | 4 | dimension validation, model metadata |
| Agent lifecycle | 5 | promote, supersede, contradict, expire, forget |
| Feedback | 5 | confidence/importance/user rating updates |
| Markdown ingestion | 6 | chunking, IDs, re-ingestion skip/update/remove |
| Vector retrieval | 7 | vector candidate search |
| Full-text retrieval | 7 | FTS candidate search |
| RRF | 7 | rank fusion correctness |
| Graph expansion | 7 | one-hop only, debug labeling |
| Reranking | 7 | bounded candidates, optional behavior |
| Scoring | 8 | normalization, weights, temporal decay |
| Privacy controls | 9 | forget/export/import/delete/redact |
| REST API | 10 | route validation, service parity |
| CLI | 10 | command parsing and service invocation |
| Evals | 11 | golden queries, metrics, reports |
| Documentation | 11 | runnable examples |

---

## 17. Suggested Pull Request Breakdown

Use small PRs to keep review and testing simple.

| PR | Scope |
|---:|---|
| PR 1 | Package skeleton, `pyproject.toml`, baseline tests |
| PR 2 | Config models, domain models, errors |
| PR 3 | ArcadeDB connection, schema, migrations |
| PR 4 | Repository CRUD and transactions |
| PR 5 | `MemoryService` and `MemoryStore` basic CRUD |
| PR 6 | FastEmbed provider and embedding validation |
| PR 7 | Agent lifecycle operations |
| PR 8 | Markdown frontmatter, chunking, hashing |
| PR 9 | Document ingestion/re-ingestion service |
| PR 10 | [DONE] Vector and full-text retrieval |
| PR 11 | [DONE] RRF, dedupe, graph expansion, reranking |
| PR 12 | [DONE] Final scoring and diagnostics |
| PR 13 | [DONE] Privacy/export/import/redaction |
| PR 14 | FastAPI routes and error mapping |
| PR 15 | CLI commands |
| PR 16 | Evals, reports, docs, examples |
| PR 17 | Hardening, acceptance checklist, release notes |

---

## 18. Recommended Build Order Rationale

The implementation order is intentionally safety-first:

1. **Models and config before persistence** so every module shares one validated contract.
2. **Schema and migrations before service methods** so data durability is reliable from the start.
3. **Embeddings safety before retrieval** so vector index corruption is avoided.
4. **Lifecycle and ingestion before retrieval** so retrieval has realistic records and statuses to filter.
5. **Retrieval before scoring** so score diagnostics are based on real pipeline components.
6. **Privacy after scoring but before adapters** so external surfaces do not expose unsafe records.
7. **REST/CLI late** so adapters cannot drive architecture drift.
8. **Evals and docs last** after behavior is stable enough to document and measure.

This aligns with `architecture.md §15` and `architecture.md §23`, which identify embedding mismatch, REST drift, stale memory, duplicate chunks, hidden scoring, privacy leakage, and overbuilt V1 as key implementation risks.

---

## 19. V1 Release Acceptance Checklist

### Package and Database

- [DONE] Package installs locally with Python 3.12+.
- [DONE] ArcadeDB Embedded initializes from Python.
- [DONE] Schema is created deterministically.
- [DONE] Stored schema version is validated at startup.
- [DONE] Migrations run idempotently.

### Agent Memory Lifecycle

- [DONE] Memory can be added.
- [DONE] Memory can be retrieved.
- [DONE] Memory can be updated.
- [DONE] Memory can be promoted.
- [DONE] Memory can be superseded.
- [DONE] Memory can be contradicted.
- [DONE] Memory can be expired.
- [DONE] Memory can be forgotten.
- [DONE] Memory can be exported.
- [DONE] User feedback updates confidence, importance, or user rating.
- [DONE] Forgotten/deleted memories are not returned by normal retrieval.

### Document Ingestion Lifecycle

- [DONE] Markdown documents ingest deterministically.
- [DONE] Chunk IDs are generated from source path, heading path, chunk index, and content hash.
- [DONE] Re-ingestion skips unchanged chunks.
- [DONE] Changed chunks are updated and re-embedded.
- [DONE] New chunks are inserted.
- [DONE] Removed chunks are deleted or marked removed based on config.

### Retrieval and Scoring

- [DONE] Hybrid retrieval performs vector search + full-text search + RRF.
- [DONE] Optional reranking works on a bounded candidate set.
- [DONE] Final scoring is configurable.
- [DONE] Search results include final score.
- [DONE] Search results include raw component scores.
- [DONE] Search results include normalized component scores.
- [DONE] Search results include debug metadata.
- [DONE] Per-memory-type temporal behavior is configurable.
- [DONE] Hybrid search returns expected golden memories from the eval fixture.

### REST/API Parity

- [DONE] REST routes call the same Python service layer as the direct API.
- [DONE] REST route handlers do not duplicate retrieval logic.
- [DONE] REST route handlers do not duplicate lifecycle logic.
- [DONE] REST route handlers do not duplicate scoring logic.
- [DONE] REST route handlers do not duplicate ingestion logic.
- [DONE] REST examples in the README are runnable.

### Operational Safety

- [DONE] Embedding dimensions are validated before insert/update.
- [DONE] Embedding model is stored.
- [DONE] Embedding model version/revision is stored when available.
- [DONE] Embedding dimensions are stored.
- [DONE] Import/export/delete-by-scope privacy controls work.
- [DONE] Evals run locally and produce a Markdown report.
- [DONE] README examples are runnable.

Reference: `architecture.md §24`.

---

## 20. Out-of-Scope for V1

Do not include these in the implementation plan for V1:

- distributed database coordination
- distributed sync
- multi-writer cluster support
- complex ontology management
- hosted vector databases
- heavy workflow orchestration frameworks
- full UI dashboard
- multi-hop graph traversal beyond one hop
- advanced authentication/authorization beyond optional local API token support
- LLM-based autonomous memory extraction unless only stubbed behind an interface

Reference: `architecture.md §2.2`, `architecture.md §3`, `architecture.md §22`.

---

## 21. Final Implementation Sequence

Build V1 in this order:

1. **Foundation:** package, config, models, errors.
2. **Persistence:** ArcadeDB connection, schema, migrations, repository.
3. **Core API:** service layer and Python `MemoryStore`.
4. **Safety:** embeddings provider, dimension validation, schema validation.
5. **State behavior:** agent lifecycle and document ingestion.
6. **Retrieval:** vector, full-text, RRF, graph expansion, reranking.
7. **Ranking:** configurable scoring and diagnostics.
8. **Governance:** privacy, import/export, delete, redact.
9. **Adapters:** REST and CLI.
10. **Quality:** evals, docs, examples, acceptance checks.

The result should be a local-first, Python-first, embedded memory store where ArcadeDB is the only persistence engine, FastEmbed provides local embeddings/reranking, retrieval is hybrid and explainable, lifecycle behavior is auditable, privacy controls are explicit, and REST/CLI remain thin wrappers over the same service layer.

Reference: `architecture.md §25`.
