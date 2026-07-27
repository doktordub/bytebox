# ByteBox Phase 0 Current System Inventory

Snapshot date: 2026-07-26

## Scope

This inventory captures the current public system surface before behavior-changing refactors begin. It is based on the checked-out repository, the current Python package metadata, the CLI and FastAPI adapters, the ArcadeDB schema helper, the example configuration, the README set, and an executed local baseline on Windows 11 with CPython 3.13.12.

## Identity Snapshot

| Surface | Current value |
|---|---|
| Source project | `doktordub/mem-store` |
| Current repository name | `bytebox` |
| Current branch | `main` |
| Distribution name | `memory-store` |
| Python package | `memory_store` |
| Package version | `0.1.0` |
| CLI name | `memory-store` |
| FastAPI title | `Memory Store` |
| Config example | `config.example.yaml` |
| Environment prefix | `MEMORY_STORE_` |
| Declared Python floor | `>=3.12` |
| Persistence backend | ArcadeDB Embedded |
| Embedding provider | FastEmbed only |
| Reranker provider | FastEmbed only |
| Schema version default | `1` |

## Public Python API

Top-level exports from `memory_store`:

- `MemoryStore`
- `MemoryStoreSettings`
- `Scope`
- `MemoryCreate`
- `MemorySearchQuery`
- `MemoryRecord`
- `HealthStatus`
- `__version__ = 0.1.0`

Current `MemoryStore` capabilities are grouped as follows.

| Capability | Methods |
|---|---|
| Construction and lifecycle | `from_config`, `close`, context manager enter/exit |
| CRUD and search | `add_memory`, `get_memory`, `update_memory`, `upsert_memory`, `search`, `search_document_chunks`, `get_chunk`, `get_chunk_context` |
| Ingestion | `ingest_document`, `ingest_folder` |
| Lifecycle and graph relations | `promote`, `supersede`, `contradict`, `expire`, `forget`, `forget_by_user`, `delete_by_scope`, `disable_memory` |
| Import/export and privacy | `export_user_memories`, `export_scope`, `import_memories`, `redact`, `add_feedback` |
| Operations | `stats`, `health` |

## CLI Surface

Current command name: `memory-store`

Current subcommands:

- `health`
- `init`
- `ingest-file`
- `ingest-folder`
- `unlock`
- `search`
- `search-chunks`
- `chunk-context`
- `eval`
- `export`
- `delete-by-scope`

Common CLI option:

- `--config <path>`

Notable current-risk CLI inputs already visible in Phase 0:

- `ingest-folder --manifest-path` lets callers choose a manifest path.
- `unlock --database-path` lets callers override the configured database path.
- `delete-by-scope --hard-delete` exposes a destructive path with no differentiated auth model because the CLI is a thin local adapter.

## Configuration Surface

Configuration precedence:

`defaults < config.yaml < environment variables < explicit code overrides`

Current sections and keys:

| Section | Keys |
|---|---|
| `database` | `path`, `create_if_missing`, `schema_version`, `embedded_single_process` |
| `embeddings` | `provider`, `model`, `model_version`, `dim`, `batch_size`, `normalize`, `dimension_mismatch` |
| `reranker` | `enabled`, `provider`, `model`, `model_version`, `top_n` |
| `retrieval` | `vector_top_n`, `fts_top_n`, `rrf_k`, `graph_expansion_enabled`, `graph_expansion_hops`, `final_top_k`, `include_component_scores`, `include_debug` |
| `scoring.weights` | `reranker`, `retrieval_fusion`, `vector`, `full_text`, `temporal`, `importance`, `confidence`, `graph`, `user_rating` |
| `scoring.temporal` | `user_preference`, `project_fact`, `task_state`, `conversation_summary`, `observation`, `error_debug_note`, `decision`, `document_chunk` |
| `chunking` | `strategy`, `max_tokens`, `overlap_tokens`, `include_heading_path`, `include_frontmatter_in_embedding`, `preserve_code_blocks`, `removed_chunk_policy` |
| `ingestion` | `max_chunks_per_document_batch`, `max_chunks_per_transaction`, `max_file_size_bytes`, `max_sections`, `max_chunks`, `max_frontmatter_bytes` |
| `privacy` | `default_sensitivity`, `allow_llm_context_default`, `allow_retrieval_default`, `delete_by_scope_requires_confirm` |
| `api` | `enabled`, `host`, `port`, `local_api_token` |
| `logging` | `level` |

Current environment-variable namespace:

- Prefix: `MEMORY_STORE_`
- Nested separator: double underscore, for example `MEMORY_STORE_DATABASE__PATH`

## REST API Surface

Current app factory: `memory_store.api.main:create_app`

Current routes:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/memories` | Create a memory |
| `GET` | `/memories/{memory_id}` | Read a memory |
| `PATCH` | `/memories/{memory_id}` | Update a memory |
| `POST` | `/memories/search` | Search memories |
| `POST` | `/chunks/search` | Search document chunks |
| `GET` | `/chunks/{chunk_id}` | Read one chunk |
| `GET` | `/chunks/{chunk_id}/context` | Read chunk context |
| `POST` | `/documents/ingest` | Ingest one document |
| `POST` | `/documents/ingest-folder` | Ingest a folder |
| `POST` | `/memories/{memory_id}/feedback` | Add feedback |
| `POST` | `/memories/{memory_id}/forget` | Forget a memory |
| `POST` | `/memories/export` | Export memories |
| `POST` | `/memories/import` | Import memories |
| `POST` | `/memories/delete-by-scope` | Delete by scope |
| `GET` | `/health` | Combined health view |
| `GET` | `/stats` | Aggregate stats |

Current API authentication behavior:

- Authentication is optional.
- When configured, the adapter checks `X-API-Token` against `settings.api.local_api_token` with direct string comparison.
- The API currently returns `{"detail": str(exc)}` for several exception types.

## Persistence and Schema Surface

Current vertex types:

- `MemoryRecord`
- `SchemaVersion`
- `MigrationRecord`
- `RedactionAudit`
- `ImportExportAudit`

Current edge types:

- `SUPERSEDES`
- `CONTRADICTS`
- `DERIVED_FROM`
- `RELATED_TO`
- `SUPPORTS`
- `MENTIONS`

Current full-text index fields:

- `title`
- `summary`
- `text`
- `tags_text`
- `source_path`

Current metadata index fields:

- `memory_id`
- `memory_type`
- `project_id`
- `user_id`
- `agent_id`
- `status`
- `created_at`
- `stable_key`
- `source_hash`
- `chunk_id`
- `section_index`
- `section_chunk_index`
- `document_chunk_index`

Current vector index:

- Vertex: `MemoryRecord`
- Field: `embedding`
- Identifier field: `memory_id`
- Distance function: `cosine`

Stored model identity fields currently persisted on `MemoryRecord`:

- `embedding_model`
- `embedding_model_version`
- `embedding_dim`
- `embedding_created_at`

Current schema-version record:

- Settings default: `database.schema_version = 1`
- Record fields: `key`, `version`, `min_compatible_version`, `updated_at`

## Documented Workflows

Current repository documentation advertises these workflows:

- Local Python API usage
- Markdown document ingestion
- Hybrid retrieval and chunk search
- FastAPI launch and direct REST calls
- Folder ingest and chunk-search example scripts
- CLI commands for init, ingest, search, export, delete, and eval
- Eval runner metrics and report generation
- Local Flask UI workflow described in `README_Ingest.md`
- Local development validation with `pytest`, `ruff`, and `mypy`

## Platform Definition

Measured baseline environment:

- OS: Windows 11 10.0.26200, AMD64
- Python: CPython 3.13.12
- `arcadedb-embedded`: 26.7.2
- `fastembed`: 0.8.0
- `fastapi`: 0.140.0
- `pydantic`: 2.13.4
- `uvicorn`: 0.51.0

Phase 0 support definition:

- Declared interpreter floor remains `>=3.12` because that is the current project metadata contract.
- Windows 11 x64 with CPython 3.13.12 is the only directly exercised runtime in this baseline.
- The Windows runtime is functional enough to run isolated tests and benchmarks, but ArcadeDB Embedded through JPype emits `Windows fatal exception: access violation` during many test flows.
- Linux and macOS support remain unverified in this repository baseline and must be confirmed before ByteBox claims a production support matrix.

## Immediate Refactor Pressure Points Captured by the Inventory

- Branding remains `memory-store` / `memory_store` / `Memory Store` across package, CLI, API title, docs, and environment variables.
- `MemoryStore` is a thin facade, but it delegates to a very large `MemoryService` implementation.
- App construction still creates the concrete store before FastAPI lifespan startup.
- The current API and CLI expose caller-controlled filesystem paths in ingestion-related flows.
- The current runtime has only one embedding/reranking backend and no offline model manifest or integrity controls.