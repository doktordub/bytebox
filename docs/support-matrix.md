# Support Matrix

## Runtime support

| Surface | Status | Notes |
|---|---|---|
| Python | Supported: `>=3.12` | Validated locally on CPython 3.13.12 |
| Windows | Preview support | Phase 0 baseline captured on Windows 11 |
| Linux | Best effort preview | No release validation captured yet in this workspace |
| macOS | Best effort preview | No release validation captured yet in this workspace |
| Embedded ArcadeDB | Supported | Single-process mode only |
| Multi-worker API | Not supported | Embedded mode must stay single process |
| Offline FastEmbed | Planned hardening | Production offline guarantees land in Phase 4 |

## Tooling support

| Tool | Status |
|---|---|
| `bytebox` CLI | Supported preview |
| Python import `bytebox` | Supported preview |
| Python import `memory_store` | Temporary transition shim |