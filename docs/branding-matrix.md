# ByteBox Branding Matrix

| Surface | Legacy | ByteBox | Notes |
|---|---|---|---|
| Repository | `mem-store` | `bytebox` | Current repository already renamed |
| Distribution | `memory-store` | `bytebox` | PyPI JSON lookup returned 404 for both `bytebox` and `bytebox-memory` on 2026-07-26; `bytebox` is currently free |
| Python package | `memory_store` | `bytebox` | `memory_store` remains a temporary shim |
| CLI | `memory-store` | `bytebox` | Canonical console script is `bytebox` |
| Config example | `config.example.yaml` | `bytebox.example.yaml` | Old filename removed |
| Env prefix | `MEMORY_STORE_` | `BYTEBOX_` | Migrated by `bytebox config migrate` in Phase 10 |
| Default data path | `./data/memory_store` | `./data/bytebox` | Prevents silent writes into legacy path |
| Default ingest manifest | `.memory_store_ingest_manifest.json` | `.bytebox_ingest_manifest.json` | Matches ByteBox artifact naming |
| API title | `Memory Store` | `ByteBox` | Exposed by FastAPI app factory |

Canonical docs and examples should use the ByteBox names only.