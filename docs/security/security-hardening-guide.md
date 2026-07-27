# ByteBox Security Hardening Guide

## Secrets

- keep API tokens, TLS key passwords, and client-key passwords out of versioned YAML;
- prefer environment variables or a secret manager;
- use `bytebox config migrate` to strip legacy secret values during migration.

## API surface

- disable or protect docs endpoints in production;
- configure trusted hosts explicitly;
- keep CORS disabled unless a reviewed deployment requires it;
- use separate scopes for read, write, ingest, export, import, delete, and admin operations.

## Filesystem ingestion

- configure `security.ingest_roots` before enabling REST ingestion;
- keep `allow_symlinks` disabled unless the ingestion root policy explicitly requires them;
- enforce file-size and chunk-count limits for bulk ingestion.

## Remote providers

- configure provider URLs only at startup;
- enforce `allowed_hosts` or `allowed_cidrs`;
- verify TLS by default outside localhost-only development;
- keep redirects disabled unless reviewed.

## Logging and diagnostics

- keep `logging.level=off` available for highly restricted environments;
- treat `X-Trace-ID` as the shared correlation handle;
- never log raw request bodies, memory text, embeddings, or secret-bearing headers.

## Release checks

- dependency audit passes;
- secret scanning passes;
- static analysis passes;
- SBOM is attached to the release artifacts;
- migration backup and rollback procedures are tested before GA.