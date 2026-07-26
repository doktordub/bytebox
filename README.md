# Memory Store

`memory-store` is a local-first Python memory system for AI agents. It keeps persistence inside ArcadeDB Embedded, exposes a Python-first API through `MemoryStore`, supports deterministic markdown ingestion, hybrid retrieval with explainable scoring, lifecycle transitions, privacy controls, and thin REST/CLI adapters over the same service layer.

See [docs/architecture.md](docs/architecture.md) for the system design and [docs/plan.md](docs/plan.md) for the phased build plan.

## What It Does

- Stores agent memories, project facts, decisions, task state, and document chunks locally.
- Ingests markdown files deterministically with stable chunk IDs and re-ingestion behavior.
- Searches with full-text + vector retrieval + reciprocal rank fusion + optional reranking.
- Preserves raw scores, normalized scores, and debug metadata on every result.
- Applies lifecycle and privacy rules before results are returned to agents or adapters.

## When To Use It

- You want local memory without a hosted vector database.
- You need one shared service layer behind Python, REST, and CLI surfaces.
- You want explainable retrieval with inspectable score components.
- You need deterministic markdown ingestion for docs, notes, or repo memory.

## When Not To Use It

- You need multi-node, distributed, or multi-writer coordination.
- You need a hosted SaaS memory service or managed vector infrastructure.
- You need multi-hop graph traversal beyond one hop.
- You need a UI dashboard or production auth model beyond a local API token.

## V1 Boundaries And Non-Goals

- Single-process embedded persistence only.
- ArcadeDB Embedded is the only persistence backend.
- FastEmbed is the only embedding and reranking provider in V1.
- REST and CLI are optional thin adapters, not separate business-logic stacks.
- No distributed sync, hosted vector DBs, or workflow orchestration framework.

## Installation

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

If you want to start from the sample configuration, copy `config.example.yaml` to a local config file and adjust the database path, API token, or retrieval settings as needed.

## Quickstart With The Python API

```python
from memory_store import MemoryStore, Scope
from memory_store.models import MemoryCreate, MemorySearchQuery, MemoryType

store = MemoryStore.from_config(
	database={"path": "./data/quickstart", "schema_version": 1},
	reranker={"enabled": False},
)

try:
	scope = Scope(project_id="arcade")
	record = store.add_memory(
		MemoryCreate(
			scope=scope,
			stable_key="decision_fastembed_reranker",
			memory_type=MemoryType.DECISION,
			title="Use FastEmbed reranking",
			text="We chose FastEmbed cross-encoder reranking for bounded hybrid search.",
		)
	)
	results = store.search(
		MemorySearchQuery(scope=scope, text="What reranking approach did we choose?", limit=3)
	)
	print(record.memory_id)
	print(results[0].memory.memory_id if results else "no results")
finally:
	store.close()
```

Runnable version: [examples/python_api_example.py](examples/python_api_example.py)

## Markdown Ingestion Example

```python
from pathlib import Path

from memory_store import MemoryStore, Scope

store = MemoryStore.from_config(database={"path": "./data/docs", "schema_version": 1})

try:
	scope = Scope(project_id="docs")
	ingest_result = store.ingest_document(Path("docs/architecture.md"), scope)
	matches = store.search_document_chunks(text="hybrid retrieval", scope=scope, limit=3)
	print(ingest_result)
	if matches:
		context = store.get_chunk_context(matches[0].chunk_id, scope=scope, before=1, after=1)
		print(matches[0].chunk_id)
		print(matches[0].metadata["approximate_token_count"])
		print(len(context.before), len(context.after))
finally:
	store.close()
```

Runnable version: [examples/markdown_ingest_example.py](examples/markdown_ingest_example.py)

`chunking.max_tokens` and `chunking.overlap_tokens` are approximate whitespace-token budgets. Persisted document chunks expose `metadata.approximate_token_count` plus the applied chunking settings so callers can inspect why a chunk is the size it is.

## Hybrid Retrieval Example

Hybrid retrieval in V1 runs these stages in order:

1. Vector candidate search.
2. Full-text candidate search.
3. Reciprocal rank fusion.
4. Deduplication and optional one-hop graph expansion.
5. Optional reranking on a bounded candidate set.
6. Final scoring with raw and normalized diagnostics preserved.

```python
results = store.search(
	MemorySearchQuery(scope=Scope(project_id="arcade"), text="service layer adapters", limit=5)
)

for result in results:
	print(result.memory.memory_id, result.final_score)
	print(result.component_scores)
	print(result.normalized_scores)
	print(result.debug)
```

## REST API Example

To launch the FastAPI adapter with the app factory:

```powershell
.\.venv\Scripts\python.exe -m uvicorn memory_store.api.main:create_app --factory --host 127.0.0.1 --port 8080
```

Example requests:

```powershell
curl http://127.0.0.1:8080/health

curl -X POST http://127.0.0.1:8080/memories/search ^
  -H "Content-Type: application/json" ^
  -d "{\"scope\": {\"project_id\": \"arcade\"}, \"text\": \"service layer\"}"

curl -X POST http://127.0.0.1:8080/chunks/search ^
	-H "Content-Type: application/json" ^
	-d "{\"scope\": {\"project_id\": \"arcade\"}, \"text\": \"hybrid retrieval\", \"before\": 1, \"after\": 1}"

curl "http://127.0.0.1:8080/chunks/chunk-id/context?project_id=arcade&before=1&after=1"
```

Runnable in-process example: [examples/rest_example.py](examples/rest_example.py)

## Bulk Ingestion and Search

Ingest all `.md` files in `doc/` folder (recursive).
```cli
python .\examples\markdown_folder_ingest_cli.py --project-id "docs" --reranker-enabled docs/
```

*Result Example*
```json
{
  "ok": true,
  "message": "Processed 6 Markdown file(s).",
  "folder": "E:\\KODE\\tools\\arcade\\docs",
  "database_path": "E:\\KODE\\tools\\arcade\\data\\memory_store",
  "scope": {
    "user_id": "",
    "project_id": "docs",
    "agent_id": ""
  },
  "matched_files": 6,
  "processed_files": 6,
  "failed_files": 0,
  "totals": {
    "added": 297,
    "updated": 0,
    "removed": 0,
    "unchanged": 0
  },
  "files": [
    {
      "path": "architecture.md",
      "added": 81,
      "updated": 0,
      "removed": 0,
      "unchanged": 0
    } 
	...
	]
}
```


Search for relavent data chunks
```cli
python .\examples\chunk_search_cli.py --project-id "docs" --reranker-enabled  "plan"
```

*Result Example*
```json
{
  "ok": true,
  "message": "Found 10 chunk result(s).",
  "query": "plan",
  "database_path": "E:\\KODE\\tools\\arcade\\data\\memory_store",
  "scope": {
    "user_id": "",
    "project_id": "docs",
    "agent_id": ""
  },
  "limit": 10,
  "before": 0,
  "after": 0,
  "include_removed": false,
  "allow_retrieval_only": true,
  "count": 10,
  "items": [
    {
      "memory_id": "22bf652c-baa3-40e3-8e28-b6faf9fab074",
      "chunk_id": "680babfb58715b517603447a9b391efa2498316e8092450e90cb406422db067b",
      "title": "Local-First Agent Memory Store \u2014 Phased Implementation Plan",
      "summary": "",
      "text": "This plan converts `architecture.md` into an actionable build sequence for implementing the local-first agent memory store. It is organized into phases with:\n\n- clear implementation goals\n- concrete deliverables\n- test deliverables\n- acceptance gates\n- dependencies\n- references back to `architecture.md`\n\nThe plan assumes V1 must remain lightweight, local-first, deterministic, and testable. The implementation should prioritize correctness, safety, lifecycle clarity, and retrieval explainability before advanced optimization.\n\n---",
      "snippet": "This plan converts `architecture.md` into an actionable build sequence for implementing the local-first agent memory store. It is organized into phases with:\n\n- clear implementation goals\n- concrete deliverables\n- test deliverables\n- acc...",
      "source_path": "E:/KODE/tools/arcade/docs/plan.md",
      "source_hash": "aeaa32f5359d9d276edf0c53d1e00c269e8d5d8e7a1ad5ae9a72e59f24b95619",
      "heading_path": [
        "Local-First Agent Memory Store \u2014 Phased Implementation Plan",
        "0. Purpose"
      ],
      "heading_path_label": "Local-First Agent Memory Store \u2014 Phased Implementation Plan / 0. Purpose",
      "section_index": 1,
      "section_chunk_index": 0,
      "document_chunk_index": 1,
      "tags": [],
      "metadata": {
        "document_lifecycle": "source_controlled",
        "heading_path_text": "Local-First Agent Memory Store \u2014 Phased Implementation Plan > 0. Purpose",
        "approximate_token_count": 68,
        "section_chunk_index": 0,
        "chunking_max_tokens": 350,
        "document_chunk_index": 1,
        "chunking_overlap_tokens": 50,
        "frontmatter": {},
        "section_index": 1,
        "chunking_preserve_code_blocks": true,
        "content_hash": "686a553c024d0add53571ccf559a4dc0affd5a1ae004b64893cce5336419f4db",
        "chunking_max_tokens_is_approximate": true
      },
      "score": 0.9184,
      "score_label": "0.918",
      "has_context": true
    }
	...
    ]
}
```


## CLI Examples

```powershell
memory-store init --config config.example.yaml
memory-store ingest-file docs/architecture.md --project-id arcade
memory-store ingest-folder docs/ --project-id arcade
memory-store search "What reranking approach did we choose?" --project-id arcade
memory-store search-chunks "hybrid retrieval" --project-id arcade
memory-store chunk-context chunk-id --project-id arcade --before 1 --after 1
memory-store export --user-id user-1 --out memories.json
memory-store delete-by-scope --project-id arcade --dry-run
memory-store eval evals/golden_queries.yaml
```

## Configuration Reference

Configuration precedence is:

```text
defaults < config.yaml < environment variables < explicit code overrides
```

Main sections in [config.example.yaml](config.example.yaml):

- `database`: local path, schema version, and create-if-missing behavior.
- `embeddings`: FastEmbed model, batch size, dimension policy.
- `reranker`: enable/disable, model, and bounded `top_n` size.
- `retrieval`: vector/FTS fan-out, RRF constant, graph expansion, debug toggles.
- `scoring`: weight map and per-memory-type temporal behavior.
- `chunking`: markdown chunking strategy, approximate token budgets, overlap, and heading-path handling.
- `privacy`: default retrieval/context flags and scope delete safety.
- `api`: host, port, and optional local API token.
- `logging`: current log level.

Environment variables use `MEMORY_STORE_` with double underscores, for example:

```powershell
$env:MEMORY_STORE_DATABASE__PATH = "./data/dev-memory"
$env:MEMORY_STORE_API__LOCAL_API_TOKEN = "dev-token"
```

## Lifecycle Examples

```python
record = store.add_memory(MemoryCreate(scope=Scope(project_id="arcade"), text="Candidate note"))
promoted = store.promote(record.memory_id, reason="confirmed during eval")

newer = store.add_memory(
	MemoryCreate(scope=Scope(project_id="arcade"), text="Updated decision", stable_key="decision:new")
)
store.supersede(promoted.memory_id, newer.memory_id, reason="replaced by new rollout rule")
store.expire(newer.memory_id, reason="time-boxed experiment ended")
store.add_feedback(newer.memory_id, MemoryFeedback(positive=True, confidence=0.9))
```

## Privacy, Delete, Export, And Import Examples

```python
scope = Scope(project_id="arcade", user_id="user-1")
export_payload = store.export_scope(scope)
store.import_memories(MemoryImport(records=export_payload.records, source="backup"), mode="upsert")
store.forget("memory-id")
store.delete_by_scope(Scope(project_id="arcade"), hard_delete=False)
store.redact([r"secret-[0-9]+"], scope=Scope(project_id="arcade"))
```

## Eval Runner Example

The eval runner loads a golden query fixture, executes searches, records latency and retrieval diagnostics, and writes both a JSON summary and a Markdown report.

```powershell
memory-store eval evals/golden_queries.yaml
```

Outputs:

- `evals/golden_queries.summary.json`
- `evals/golden_queries.report.md`

Metrics included by V1:

- Recall@10
- MRR
- NDCG
- Latency p50/p95
- Reranker time plus input-document count as the local cost proxy
- Duplicate rate
- Stale memory rate

Runnable version: [examples/eval_runner_example.py](examples/eval_runner_example.py)

## Local Development And Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check memory_store evals examples tests
.\.venv\Scripts\python.exe -m mypy memory_store tests
```

## Known V1 Limitations

- Single-process embedded persistence only.
- One-hop graph expansion only.
- No hosted or distributed sync layer.
- REST authentication is limited to an optional local API token.
- Golden query fixtures are intentionally project-specific; update `evals/golden_queries.yaml` as your corpus evolves.
- On Windows, `arcadedb_embedded` via JPype can emit `Windows fatal exception: access violation` during some threaded pytest flows even when the process exit code is `0`. Treat the command exit code as the source of truth.
