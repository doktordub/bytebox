# Memory Store UI

Local Flask console for the `memory_store` package. The UI runs directly against the configured Python API, exposes runtime health and stats, supports manual memory creation plus both memory and chunk search, and lets you edit, reload, and reset the active database from the browser.

## Prerequisites

- Use the repo-local virtual environment at `.venv/`.
- The virtual environment must already contain `memory_store` and its transitive dependencies.
- The default runtime config lives at `ui/config.yaml` and normalizes relative database paths into the repo `data/` folder.

## Install memory_store

```powershell
.\.venv\Scripts\python.exe -m pip install  .\dist\memory_store-0.1.0.tar.gz

.\.venv\Scripts\python.exe -m pip install  .\dist\memory_store-0.1.0-py3-none-any.whl
```

## Run Locally

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r ui\requirements.txt
.\.venv\Scripts\python.exe ui\app.py
```

Open `http://127.0.0.1:5000/`.


## Bulk Data Ingestion

Ingest all `.md` files in `doc/` folder (recursive).
```powershell
.\.venv\Scripts\python.exe .\examples\markdown_folder_ingest_cli.py --project-id "docs" --reranker-enabled docs/
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

## Search for Chunks

Search for relavent data chunks
```powershell
.\.venv\Scripts\python.exe .\examples\chunk_search_cli.py --project-id "docs" --reranker-enabled  "plan"
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


