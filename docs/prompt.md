You are an expert senior Python engineer specializing in Agentic AI, local-first retrieval systems, embedded databases, production-quality Python package design, and retrieval evaluation.

Your task is to design and implement a lightweight, portable, local-first Python memory store for AI agents.

The system must use **ArcadeDB Embedded** as the single local persistence engine for:

- Agent memory records
- Source-controlled document chunks
- Vector indexes
- Full-text indexes
- Metadata indexes
- Lightweight graph relationships

FastEmbed must provide local dense embeddings and optional local reranking.

The primary interface must be a Python-first `MemoryStore` API. A thin REST API may wrap the same service layer, but REST must not become the core architecture.

---

# 1. Product Goal

Build a reusable Python module that lets AI agents persist, retrieve, update, score, expire, supersede, contradict, forget, redact, import/export, and evaluate memories.

The memory store must support two related but distinct record families:

## 1.1 Agent memories

Examples:

- User preferences
- Project facts
- Task states
- Conversation summaries
- Decision memories
- Observation memories
- Episodic memories
- Error/debug notes

Agent memories are mutable knowledge records. They may be promoted, updated, superseded, contradicted, expired, forgotten, or corrected by user feedback.

## 1.2 Document chunks

Examples:

- Markdown sections
- Architecture document paragraphs
- Code blocks
- Source-controlled knowledge chunks

Document chunks are source-controlled records. They should be replaced through deterministic re-ingestion rather than manually promoted/superseded like agent memories.

Agent memories and document chunks must share retrieval infrastructure, but they must have separate lifecycle rules.

---

# 2. V1 Boundaries

Implement V1 with strict boundaries.

## 2.1 V1 must include

- Embedded/single-process ArcadeDB first.
- Python `MemoryStore` interface as the primary API.
- Thin FastAPI REST wrapper second.
- Deterministic markdown ingestion.
- FastEmbed local embeddings.
- Optional FastEmbed reranking.
- Vector search + full-text search + Reciprocal Rank Fusion.
- Configurable final scoring.
- One-hop graph expansion only.
- Explicit memory lifecycle controls.
- Privacy controls for export, import, forget, delete-by-scope, and disable-by-scope.
- Schema versioning and migrations.
- Local evals with golden queries.

## 2.2 V1 must not include

- Distributed sync.
- Multi-writer cluster support.
- Complex ontology management.
- Multi-hop graph traversal beyond one hop.
- Hosted vector databases.
- Heavy workflow orchestration frameworks.
- Full UI dashboard.
- LLM-based autonomous memory extraction unless stubbed behind an interface.
- Advanced authentication/authorization beyond optional local API token support.

Design choices should favor local reliability, deterministic behavior, observability, and simple APIs over distributed complexity.

---

# 3. Tech Stack

Use:

- Python 3.12+ (already installed in .venv/ folder)
- `arcadedb-embedded` (already installed in .venv/Lib/site-packages/arcadedb_embedded folder)
- FastEmbed for embeddings
- FastEmbed reranker as an optional reranking stage
- FastAPI for the thin REST wrapper
- Pydantic v2 for typed models and validation
- PyYAML or equivalent for `config.yaml`
- pytest for tests
- ruff and mypy-friendly code style

Avoid heavyweight dependencies unless clearly justified.

---

# 4. Package Structure

Create a small Python package with this structure:

```text
memory_store/
  __init__.py
  config.py
  errors.py
  models.py
  store.py
  service.py
  scoring.py
  lifecycle.py
  privacy.py
  cli.py
  ingestion/
    __init__.py
    markdown.py
    chunking.py
    hashing.py
    frontmatter.py
  retrieval/
    __init__.py
    vector.py
    full_text.py
    fusion.py
    rerank.py
    graph.py
    filters.py
  arcade/
    __init__.py
    connection.py
    schema.py
    queries.py
    migrations.py
    transactions.py
  embeddings/
    __init__.py
    fastembed_provider.py
    validation.py
  api/
    __init__.py
    main.py
    routes.py
    schemas.py
  evals/
    golden_queries.yaml
    runner.py
    metrics.py
    report.py
  tests/
    test_store.py
    test_ingestion.py
    test_retrieval.py
    test_lifecycle.py
    test_scoring.py
    test_privacy.py
    test_api.py
  examples/
    python_api_example.py
    markdown_ingest_example.py
    rest_example.py
    eval_runner_example.py
  pyproject.toml
  README.md
  config.example.yaml
```

Architecture boundaries:

- `store.py` exposes the public Python `MemoryStore` interface.
- `service.py` contains the shared service layer used by both Python and REST.
- `arcade/` contains all ArcadeDB-specific persistence logic.
- `embeddings/` contains embedding and dimension validation logic.
- `retrieval/` contains filters, vector search, full-text search, RRF, reranking, graph expansion, and result assembly.
- `ingestion/` contains deterministic markdown ingestion.
- `lifecycle.py` handles agent memory lifecycle transitions.
- `privacy.py` handles export/import/delete/forget/redact controls.
- `api/` wraps the same service layer with REST routes.
- `evals/` validates retrieval quality and scoring behavior.

Do not duplicate business logic between Python API and REST routes.

---

# 5. Public Python API

Design and implement a clean `MemoryStore` interface.

Required methods:

```python
class MemoryStore:
    @classmethod
    def from_config(cls, config_path: str | Path, **overrides: Any) -> "MemoryStore": ...

    def add_memory(self, memory: MemoryCreate) -> MemoryRecord: ...
    def get_memory(self, memory_id: str) -> MemoryRecord | None: ...
    def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord: ...
    def upsert_memory(self, memory: MemoryCreate, stable_key: str | None = None) -> MemoryRecord: ...
    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]: ...

    def ingest_document(self, path: str | Path, scope: Scope) -> IngestResult: ...
    def ingest_folder(self, path: str | Path, scope: Scope) -> FolderIngestResult: ...

    def promote(self, memory_id: str, reason: str | None = None) -> MemoryRecord: ...
    def supersede(self, old_memory_id: str, new_memory_id: str, reason: str | None = None) -> None: ...
    def contradict(self, memory_id_a: str, memory_id_b: str, reason: str | None = None) -> None: ...
    def expire(self, memory_id: str, reason: str | None = None) -> None: ...
    def forget(self, memory_id: str) -> None: ...

    def forget_by_user(self, user_id: str) -> int: ...
    def delete_by_scope(self, scope: Scope, hard_delete: bool = False) -> int: ...
    def disable_memory(self, scope: Scope) -> int: ...
    def export_user_memories(self, user_id: str) -> list[MemoryRecord]: ...
    def export_scope(self, scope: Scope) -> MemoryExport: ...
    def import_memories(self, payload: MemoryImport, mode: ImportMode = "upsert") -> ImportResult: ...
    def redact(self, patterns: list[str], scope: Scope | None = None) -> RedactionResult: ...

    def add_feedback(self, memory_id: str, feedback: MemoryFeedback) -> MemoryRecord: ...
    def stats(self) -> MemoryStats: ...
    def health(self) -> HealthStatus: ...
```

The API must support both code configuration and file-based configuration.

Configuration precedence:

```text
defaults < config.yaml < environment variables < explicit code arguments
```

---

# 6. Memory Types

Support these memory types:

| Memory Type | Purpose | Default Retention |
|---|---|---|
| `user_preference` | Stable user preference likely useful across tasks | Long |
| `project_fact` | Stable project-level fact | Long |
| `task_state` | Current or recent task context | Medium |
| `conversation_summary` | Condensed summary of prior conversation | Medium |
| `decision` | Explicit decision or architectural choice | Long |
| `observation` | Raw observed event, tool result, error, or note | Short / Medium |
| `episodic` | Time-specific event memory | Medium |
| `error_debug_note` | Debugging issue, failure, tool error, environment note | Short / Medium |
| `document_chunk` | Source-controlled chunk from markdown/docs | Source-controlled |

Important rules:

- Do not promote every observation to long-term memory.
- Observations should start as low-stability records.
- Promote only when useful, repeated, confirmed, or project-relevant.
- Store low-confidence extractions as `observation`, not `project_fact`.
- User-confirmed memories should receive higher confidence and importance.

Promotion flow:

```text
raw observation
  -> candidate memory
  -> usefulness scoring
  -> active memory
  -> updated / expired / superseded over time
```

Promotion signals:

| Signal | Example |
|---|---|
| Repeated mention | User repeatedly discusses LocalAI CUDA backend |
| Explicit preference | “I prefer lightweight Python frameworks.” |
| Project-level fact | “BB1 uses frontend/backend/MCP tiers.” |
| Decision | “Use FastEmbed reranker after hybrid retrieval.” |
| High future utility | Constraint likely to matter in future tasks |

---

# 7. Core Data Model

Create typed Pydantic models for memory records.

Each memory record must include at minimum:

```python
memory_id: str
stable_key: str | None
scope: Scope
user_id: str | None
project_id: str | None
agent_id: str | None

memory_type: MemoryType
status: MemoryStatus
sensitivity: SensitivityLevel

title: str | None
summary: str | None
text: str
tags: list[str]

source_type: SourceType | None
source_path: str | None
source_hash: str | None
source_uri: str | None
chunk_id: str | None
heading_path: list[str] | None
chunk_index: int | None

embedding: list[float] | None
embedding_model: str | None
embedding_model_version: str | None
embedding_dim: int | None
embedding_created_at: datetime | None

confidence: float
importance: float
user_rating: float | None

valid_from: datetime | None
valid_to: datetime | None
created_at: datetime
updated_at: datetime
last_accessed_at: datetime | None
expires_at: datetime | None

allow_retrieval: bool
allow_llm_context: bool
retention_policy: str | None

version: int
schema_version: int
superseded_by: str | None
metadata: dict[str, Any]
```

Recommended enums:

```python
MemoryStatus = Literal[
    "active",
    "candidate",
    "superseded",
    "contradicted",
    "expired",
    "deleted",
    "removed",
    "forgotten"
]

SensitivityLevel = Literal[
    "public",
    "internal",
    "private",
    "sensitive"
]
```

Search results must include raw and normalized component scores:

```python
class MemorySearchResult(BaseModel):
    memory: MemoryRecord
    final_score: float
    component_scores: dict[str, float]
    normalized_scores: dict[str, float]
    debug: dict[str, Any]
```

---

# 8. ArcadeDB Schema Requirements

Create an ArcadeDB schema that supports:

- Memory records
- Document chunks
- Edges between memories
- Vector index
- Full-text index
- Metadata indexes
- Stored schema version
- Migration history

Required graph edge types:

| Edge | Meaning |
|---|---|
| `SUPERSEDES` | New memory replaces old memory |
| `CONTRADICTS` | Two records conflict |
| `DERIVED_FROM` | Memory extracted from source document or conversation |
| `RELATED_TO` | Soft relationship between memories |
| `SUPPORTS` | One record supports another |
| `MENTIONS` | Memory mentions project/user/entity/topic |

ArcadeDB-specific requirements:

- Use cosine similarity for text embeddings.
- Store and validate `embedding_model`, `embedding_model_version`, and `embedding_dim`.
- Full-text index these fields: `title`, `summary`, `text`, `tags`, `source_path`.
- Metadata index these fields: `memory_type`, `project_id`, `user_id`, `agent_id`, `status`, `created_at`, `stable_key`, `source_hash`, `chunk_id`.
- Use transactions for batch insert/update.
- Limit graph expansion to one hop in V1.
- Use embedded mode for local tools, tests, and single-process agents.
- Keep FastAPI REST wrapper separate from ArcadeDB internals.
- Include deterministic schema creation.
- Include migrations with monotonic schema version numbers.

---

# 9. Separate Lifecycle Rules

Agent memories and document chunks must not use the same lifecycle workflow.

## 9.1 Agent memory lifecycle

Agent memories support:

- `add`
- `upsert`
- `promote`
- `update`
- `supersede`
- `contradict`
- `expire`
- `forget`
- `redact`
- `feedback`

Deduplication behavior:

| Case | Action |
|---|---|
| Same fact, newer wording | Update same `stable_key`; increment version |
| Same fact, more detail | Merge text; preserve previous version |
| Same fact, changed value | Mark previous as `superseded`; link `SUPERSEDES` |
| True contradiction | Keep both; link `CONTRADICTS`; lower confidence until resolved |
| Low-confidence extraction | Store as `observation`, not `project_fact` |
| User explicitly confirms | Promote confidence and importance |
| User corrects memory | Mark old memory superseded immediately |

## 9.2 Document chunk lifecycle

Document chunks support:

- deterministic source parsing
- deterministic chunk IDs
- deterministic source hashes
- skip unchanged chunks
- update and re-embed changed chunks
- insert new chunks
- delete or mark removed chunks when source disappears
- optional relink when file is renamed but content is unchanged

Document chunks should not be manually superseded as normal agent memories unless explicitly converted into agent memories.

---

# 10. Markdown Ingestion

Implement deterministic markdown ingestion.

Markdown files may include frontmatter:

```yaml
---
name: BB1 POC Architecture
description: Frontend, backend, and MCP architecture document
tags:
  - python
  - agent-framework
  - fastmcp
version: "0.1"
owner: architecture-team
---
```

Frontmatter field limits:

| Field | Limit |
|---|---:|
| `name` | 1024 chars |
| `description` | 8192 chars |
| `version` | 32 chars |
| `owner` | 256 chars |
| `tags` | array of strings |

Default chunking config:

```yaml
chunking:
  strategy: markdown_section
  max_tokens: 350
  overlap_tokens: 50
  include_heading_path: true
  include_frontmatter_in_embedding: true
  preserve_code_blocks: true
```

Do not use sentence-level chunking as the default. Prefer section or paragraph chunks with heading context.

When embedding markdown chunks, do not embed only the body. Construct a searchable embedding string:

```text
Title: BB1 POC Architecture
Description: Frontend, backend, MCP tiers
Tags: python, agent-framework, fastmcp
Section: Backend > Memory Store
Content: ...
```

Deterministic chunk ID:

```python
chunk_id = sha256(
    source_path + heading_path + chunk_index + content_hash
)
```

Re-ingestion behavior:

| File Change | Action |
|---|---|
| Same content hash | Skip |
| Same section, changed text | Update + re-embed |
| New section | Insert |
| Removed section | Mark deleted/removed or hard-delete based on config |
| Renamed file but same content | Optionally relink |

Acceptance requirement:

- Re-ingesting the same folder twice must produce the same chunk IDs and skip unchanged chunks.

---

# 11. Retrieval Pipeline

Implement hybrid retrieval with this pipeline:

1. Normalize query.
2. Extract structured filters.
3. Apply hard filters:
   - `user_id`
   - `project_id`
   - `agent_id`
   - `memory_type`
   - `status = active`
   - `source_type`
   - `sensitivity`
   - `allow_retrieval = true`
4. Dense vector search top N.
5. Full-text search top N.
6. Merge results using Reciprocal Rank Fusion.
7. Deduplicate by:
   - `memory_id`
   - `stable_key`
   - `chunk_id`
   - `source_hash`
8. Optional one-hop graph expansion.
9. Rerank top 40-80 candidates if reranker is enabled.
10. Apply final configurable scoring.
11. Return top K with final score, component scores, normalized scores, and debug metadata.

Use RRF for first-pass fusion:

```python
rrf_score = 1 / (k + vector_rank) + 1 / (k + fts_rank)
```

Do not directly mix raw vector scores and BM25 scores before rank fusion.

Graph expansion rules:

- V1 graph expansion is maximum one hop.
- Graph expansion must be optional and configurable.
- Graph-expanded records must be labeled in debug metadata.
- Graph score must be separate from vector/FTS scores.

---

# 12. Configurable Final Scoring

Implement configurable final scoring with default weights:

```python
final_score =
    0.45 * reranker_score_norm
  + 0.15 * retrieval_fusion_score_norm
  + 0.10 * vector_score_norm
  + 0.08 * fts_score_norm
  + 0.07 * temporal_score_norm
  + 0.06 * importance_score_norm
  + 0.04 * confidence_score_norm
  + 0.03 * graph_score_norm
  + 0.02 * user_rating_score_norm
```

Requirements:

- Normalize every score component before final scoring.
- Store raw component scores in `MemorySearchResult.component_scores`.
- Store normalized component scores in `MemorySearchResult.normalized_scores`.
- Make weights configurable globally.
- Allow scoring overrides per memory type.
- Allow per-memory-type temporal behavior.
- If reranking is disabled, redistribute reranker weight across retrieval/vector/FTS components using a documented strategy.
- If a component is unavailable, avoid producing misleading scores.
- Include debug metadata explaining which components contributed to the final score.

Temporal scoring default:

```python
temporal_score = exp(-age_days / half_life_days)
```

Default half-lives:

| Memory Type | Half-life |
|---|---:|
| `user_preference` | 365 days |
| `project_fact` | 180 days |
| `task_state` | 30 days |
| `conversation_summary` | 60 days |
| `observation` | 14 days |
| `error_debug_note` | 21 days |
| `document_chunk` | No decay / source-based |
| `decision` | No decay unless superseded |

Temporal behavior rules:

| Memory Type | Behavior |
|---|---|
| `user_preference` | Very slow decay; prefer latest if contradicted |
| `project_fact` | Moderate decay unless source-controlled |
| `task_state` | Strong recency boost |
| `observation` | Strong decay |
| `error_debug_note` | Strong decay unless current task references it |
| `decision` | No decay unless superseded |
| `document_chunk` | No decay; source version controls freshness |

---

# 13. Operational Safety Requirements

Implement these safety controls before optimizing advanced retrieval behavior.

## 13.1 Embedding safety

- Validate embedding dimensions before insert/update.
- Store `embedding_model`.
- Store `embedding_model_version` or revision if available.
- Store `embedding_dim`.
- Reject or quarantine records when embedding dimensions do not match the active index.
- Include a clear migration path for re-embedding when the model changes.

## 13.2 Schema safety

- Store a database schema version.
- Store migration history.
- Make schema creation deterministic.
- Migrations must be idempotent where practical.
- Startup must validate schema compatibility.

## 13.3 Privacy and data controls

Required methods:

```python
memory_store.forget(memory_id)
memory_store.forget_by_user(user_id)
memory_store.delete_by_scope(scope, hard_delete=False)
memory_store.disable_memory(scope)
memory_store.export_user_memories(user_id)
memory_store.export_scope(scope)
memory_store.import_memories(payload, mode="upsert")
memory_store.redact(patterns=[...], scope=scope)
```

Privacy metadata:

```python
sensitivity: Literal["public", "internal", "private", "sensitive"]
retention_policy: str
allow_retrieval: bool
allow_llm_context: bool
```

Rules:

- `forgotten` memories must not be retrievable.
- `deleted` records must not be retrievable.
- `removed` document chunks must not be retrievable unless `include_removed=true` is explicitly requested for audit/debug.
- `allow_llm_context = false` means the memory can be found internally but must not be returned as LLM prompt context.
- `sensitive` memories must require explicit retrieval permission.
- Redaction must preserve audit metadata without exposing redacted text.
- Delete/export/disable operations must support scope-level filtering.

---

# 14. REST API Wrapper

Build a thin FastAPI wrapper around the Python service layer.

Required endpoints:

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

REST rules:

- REST routes must call the same service layer used by the Python API.
- Do not duplicate retrieval or lifecycle logic in route handlers.
- Include request/response Pydantic schemas.
- Include example curl requests in the README.
- Include clear error responses for validation failures, missing records, schema mismatch, and embedding dimension mismatch.
- Keep REST optional in V1.

---

# 15. Configuration

Provide `config.example.yaml`.

Example:

```yaml
database:
  path: "./data/memory_store"
  create_if_missing: true
  schema_version: 1
  embedded_single_process: true

embeddings:
  provider: fastembed
  model: "BAAI/bge-small-en-v1.5"
  model_version: null
  dim: 384
  batch_size: 64
  normalize: true
  dimension_mismatch: "error"  # error | quarantine | reembed

reranker:
  enabled: true
  provider: fastembed
  model: "Xenova/ms-marco-MiniLM-L-6-v2"
  model_version: null
  top_n: 60

retrieval:
  vector_top_n: 30
  fts_top_n: 30
  rrf_k: 60
  graph_expansion_enabled: true
  graph_expansion_hops: 1
  final_top_k: 10
  include_component_scores: true
  include_debug: true

scoring:
  weights:
    reranker: 0.45
    retrieval_fusion: 0.15
    vector: 0.10
    full_text: 0.08
    temporal: 0.07
    importance: 0.06
    confidence: 0.04
    graph: 0.03
    user_rating: 0.02
  temporal:
    user_preference:
      behavior: slow_decay
      half_life_days: 365
    project_fact:
      behavior: moderate_decay
      half_life_days: 180
    task_state:
      behavior: strong_recency
      half_life_days: 30
    conversation_summary:
      behavior: moderate_decay
      half_life_days: 60
    observation:
      behavior: strong_decay
      half_life_days: 14
    error_debug_note:
      behavior: strong_decay
      half_life_days: 21
    decision:
      behavior: no_decay_unless_superseded
      half_life_days: null
    document_chunk:
      behavior: source_version_controls_freshness
      half_life_days: null

chunking:
  strategy: markdown_section
  max_tokens: 350
  overlap_tokens: 50
  include_heading_path: true
  include_frontmatter_in_embedding: true
  preserve_code_blocks: true
  removed_chunk_policy: mark_removed  # mark_removed | hard_delete

privacy:
  default_sensitivity: internal
  allow_llm_context_default: true
  allow_retrieval_default: true
  delete_by_scope_requires_confirm: true

api:
  enabled: false
  host: "127.0.0.1"
  port: 8080
  local_api_token: null

logging:
  level: INFO
```

Configuration precedence:

```text
defaults < config.yaml < environment variables < explicit code arguments
```

---

# 16. CLI and Mass Embedding Utility

Provide a lightweight CLI for local workflows.

Example commands:

```bash
memory-store init --config config.yaml
memory-store ingest-file docs/architecture.md --project-id bb1 --user-id user-1
memory-store ingest-folder docs/ --project-id bb1 --user-id user-1
memory-store search "What reranking approach did we choose?" --project-id bb1
memory-store eval evals/golden_queries.yaml
memory-store export --user-id user-1 --out memories.json
memory-store delete-by-scope --project-id bb1 --dry-run
```

The CLI must use the same Python service layer as the direct API and REST wrapper.

---

# 17. Evaluation Suite

Create an `evals/` folder with golden queries.

Example `golden_queries.yaml`:

```yaml
queries:
  - query: "What reranking approach did we choose?"
    expected_memory_ids:
      - decision_fastembed_reranker

  - query: "What is the BB1 architecture?"
    expected_memory_types:
      - project_fact
      - document_chunk

  - query: "What LocalAI issue was the user debugging?"
    expected_memory_types:
      - task_state
      - observation
```

Required metrics:

| Metric | Purpose |
|---|---|
| Recall@10 | Did the right memory appear? |
| MRR | Did it rank near the top? |
| NDCG | Ranking quality |
| Latency p50/p95 | Agent usability |
| Reranker cost/time | Keeps retrieval bounded |
| Duplicate rate | Measures ingestion quality |
| Stale memory rate | Measures lifecycle quality |

The eval runner must output:

- JSON summary
- Markdown report
- Per-query result details
- Component score breakdown
- Raw score and normalized score diagnostics

Acceptance requirement:

- Hybrid search must return expected golden memories for the included eval fixture.

---

# 18. Testing Requirements

Add pytest tests for:

- Creating the database schema
- Running migrations
- Validating schema version
- Adding and retrieving a memory
- Updating a memory by `stable_key`
- Promoting a candidate memory
- Superseding memory
- Contradicting memories
- Expiring memory
- Forgetting memory
- Exporting user memories
- Importing memories
- Delete-by-scope dry run and execution
- Markdown chunking
- Deterministic chunk IDs
- Re-ingesting unchanged markdown
- Re-ingesting changed markdown
- Marking removed chunks when source disappears
- Embedding dimension validation
- Hybrid retrieval with mocked vector/FTS results
- RRF merge behavior
- One-hop graph expansion
- Score normalization
- Component score persistence in search results
- API route validation
- REST route calls shared service layer

Tests must be deterministic and runnable locally.

---

# 19. Documentation Requirements

Create a clear `README.md` with:

1. What this package does
2. When to use it
3. When not to use it
4. V1 boundaries and non-goals
5. Installation
6. Quickstart with Python API
7. Markdown ingestion example
8. Hybrid retrieval example
9. REST API example
10. CLI examples
11. Configuration reference
12. Lifecycle examples
13. Privacy/delete/export/import examples
14. Eval runner example
15. Known V1 limitations

Include this Python example:

```python
from memory_store import MemoryStore, MemoryCreate, Scope

store = MemoryStore.from_config("config.yaml")

memory = store.add_memory(
    MemoryCreate(
        scope=Scope(user_id="user-1", project_id="bb1"),
        memory_type="decision",
        title="Use FastEmbed reranker",
        text="Use FastEmbed reranker after hybrid retrieval.",
        confidence=0.95,
        importance=0.8,
        tags=["retrieval", "reranking", "fastembed"],
    )
)

results = store.search("What reranking approach did we choose?")

for result in results:
    print(result.final_score, result.memory.title, result.component_scores)
```

---

# 20. V1 Non-Goals

Do not implement these in V1:

- Distributed database coordination
- Distributed sync
- Multi-writer cluster support
- Complex ontology management
- LLM-based autonomous memory extraction unless stubbed behind an interface
- External hosted vector databases
- Heavy workflow orchestration frameworks
- Multi-hop graph traversal beyond one hop
- UI dashboard
- Authentication/authorization beyond simple local API token support

---

# 21. Acceptance Criteria

The implementation is successful when:

## 21.1 Package and database

- The package installs locally with Python 3.12+.
- ArcadeDB Embedded initializes from Python.
- The schema is created deterministically.
- The stored schema version is validated at startup.
- Migrations can run idempotently.

## 21.2 Agent memory lifecycle

- A memory can be added, retrieved, updated, promoted, superseded, contradicted, expired, forgotten, and exported.
- User feedback updates confidence, importance, or user rating.
- Forgotten/deleted memories are not returned by normal retrieval.

## 21.3 Document ingestion lifecycle

- Markdown documents can be ingested deterministically.
- Deterministic chunk IDs are generated using source path, heading path, chunk index, and content hash.
- Re-ingestion skips unchanged chunks.
- Changed chunks are updated and re-embedded.
- New chunks are inserted.
- Removed chunks are deleted or marked removed based on config.

## 21.4 Retrieval and scoring

- Hybrid retrieval performs vector search + full-text search + RRF.
- Optional reranking works on a bounded candidate set.
- Final scoring is configurable.
- Search results include final score, raw component scores, normalized component scores, and debug metadata.
- Per-memory-type temporal behavior is configurable.
- Hybrid search returns expected golden memories from the eval fixture.

## 21.5 REST/API parity

- REST routes call the same Python service layer as the direct API.
- REST route handlers do not duplicate retrieval, lifecycle, scoring, or ingestion logic.
- REST examples in the README are runnable.

## 21.6 Operational safety

- Embedding dimensions are validated before insert/update.
- Embedding model, model version/revision, and dimensions are stored.
- Import/export/delete-by-scope privacy controls work.
- Evals run locally and produce a markdown report.
- README examples are runnable.

---

# 22. Implementation Style

Write production-quality but lightweight code.

Use:

- Clear type hints
- Pydantic models
- Small modules
- Minimal dependencies
- Explicit errors
- Deterministic IDs
- Configurable defaults
- Helpful logging
- Tests for core behavior
- Auditable lifecycle changes
- Inspectable scoring diagnostics

Avoid:

- Hidden global state
- Overly abstract plugin systems
- Premature distributed architecture
- Scoring logic that cannot be inspected
- Retrieval results without score explanations
- Lifecycle changes without audit metadata
- REST logic that diverges from Python API behavior

