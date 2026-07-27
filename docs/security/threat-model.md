# ByteBox Phase 0 Threat Model

Snapshot date: 2026-07-26

## Scope

This threat model covers the current repository baseline and the specific Phase 0 review areas called out in the ByteBox plan:

- local filesystem ingestion;
- API trust boundaries;
- embedded database files;
- model files and supply chain;
- Ollama / llama.cpp network connections;
- import/export and destructive operations;
- logs and telemetry.

## Assumptions

- The current implementation is a single-process, local-first service built around ArcadeDB Embedded.
- The current API uses an optional static token and is not yet a production-grade auth boundary.
- The current embedding and reranking runtime is FastEmbed-only.
- Future ByteBox phases will add outbound HTTP providers, TLS, richer auth, and structured logging, but those controls do not exist yet in the baseline.

## Trust Boundaries

1. Caller to CLI or REST adapter.
2. CLI or REST adapter to `MemoryStore` / `MemoryService`.
3. Service layer to local filesystem for ingest, manifests, import, and export.
4. Service layer to ArcadeDB Embedded files and locks.
5. Service layer to model artifacts and Hugging Face downloads.
6. Future service layer to local-network Ollama / llama.cpp endpoints.
7. Service layer to logs, stderr, health, and stats output.

## Risk Register

| Surface | Threat | Current exposure | Severity | Required ByteBox control |
|---|---|---|---|---|
| Filesystem ingestion | Path traversal, symlink escape, unexpected file types, oversized files | API and CLI accept caller-provided ingest paths; folder ingest also accepts manifest output paths | High | Constrain ingest to configured roots after canonicalization and symlink policy checks; generate manifests only under ByteBox-owned state paths |
| REST trust boundary | Unauthorized read/write/delete/admin actions | Optional static token only; no differentiated scopes; direct token comparison | High | Timing-safe secret handling, explicit scopes, and replaceable auth provider port |
| Error handling | Secret and implementation leakage | FastAPI adapter returns raw exception strings in JSON `detail` fields | High | Stable safe error envelope with trace ID; redact internal exception content |
| Embedded database files | Corruption, lock contention, accidental multi-process access | Database opens during service construction; embedded mode assumes one process but does not yet own startup in lifespan | High | One database owner per process, explicit startup/shutdown management, and `workers == 1` validation |
| Model supply chain | Uncontrolled runtime download, tampered artifacts, unknown provenance | FastEmbed downloads from Hugging Face at runtime; no checksum, manifest, or offline-only mode; Windows cache behavior degraded by symlink limitations | High | Local model manifests, checksum verification, offline startup mode, no runtime downloads |
| Import/export | Data exfiltration, destructive overwrite, malformed payloads | Export/import and delete flows exist without an operator-grade privilege split | High | Separate scopes for export/import/delete/admin, explicit confirmation and validation controls |
| Logging and telemetry | Content or secret leakage | Logs are not centrally structured or redacted; current API errors expose raw exception text | High | One logging bootstrap, redaction processor, trace IDs, safe event vocabulary |
| Future outbound provider HTTP | SSRF, redirect abuse, metadata access, TLS downgrade | Not implemented yet, but Phase 5 adds local-network HTTP providers | High | Static startup-only endpoints, allowlists, redirect policy, TLS verification, and DNS/IP validation |
| Availability | Unbounded work, blocking I/O, heavy per-request model/runtime construction | Store startup opens DB immediately; reranker runtime is request-scoped; search does significant Python-side work | Medium | Lifespan-owned shared runtimes, bounded concurrency, backpressure, and indexed candidate selection |
| Docs and operational surfaces | Accidental path, token, or system-detail exposure | `/health` and `/stats` are coarse endpoints; README and examples expose local absolute path examples | Medium | Separate live/ready/status/state contracts with sanitized output |

## Threat Notes By Plan Category

### Local filesystem ingestion

Current baseline issues:

- Ingest requests can carry arbitrary local paths.
- Folder ingest allows a caller-supplied manifest path.
- The public plan already identifies these as unacceptable for production.

### API trust boundaries

Current baseline issues:

- The current API does not separate read, write, ingest, delete, export/import, or admin operations.
- The authentication primitive is a single optional token.
- The current adapter trusts direct token equality and exposes internal error strings.

### Embedded database files

Current baseline issues:

- The database opens before FastAPI lifespan startup.
- Test execution on Windows produces native access-violation traces in ArcadeDB / JPype flows.
- Database path details appear in example outputs and health-oriented workflows.

### Model files and supply chain

Current baseline issues:

- The current FastEmbed path has no local manifest, checksum, or offline startup contract.
- A real first-run measurement triggered a Hugging Face download and Windows symlink-privilege warning.
- Model-license facts are external to the repo and not yet recorded in the project tree.

### Ollama / llama.cpp network connections

Current baseline issues:

- These providers are not yet implemented.
- The plan correctly treats them as startup-configured services rather than caller-controlled URLs.
- SSRF and TLS controls must be designed before enabling these providers.

### Import/export and destructive operations

Current baseline issues:

- Export, import, forget, and delete-by-scope are already part of the shared service surface.
- There is no privilege split between routine search and destructive operations.

### Logs and telemetry

Current baseline issues:

- Logging configuration exists, but a redaction and trace-context system does not.
- Native library warnings and errors currently reach stdout/stderr directly.
- Raw exception text is returned by the current FastAPI adapter.

## Highest-Priority Actions Carried Forward

1. Move resource ownership into an explicit application lifespan and reject multi-worker embedded execution.
2. Replace raw exception output with safe, stable error contracts and trace IDs.
3. Lock down ingest paths, manifest generation, and destructive operations.
4. Make model artifacts explicit, local, and checksum-verifiable.
5. Add structured logging and redaction before expanding the network-facing surface.

## Gate Result

Phase 0 threat modeling is complete. The current highest-risk areas are now explicit and directly mapped to later ByteBox phases instead of remaining implicit operational debt.