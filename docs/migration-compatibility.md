# Migration Compatibility Policy

## Phase 1 policy

ByteBox treats the rebrand as a product rename, not a permanent dual-brand codebase.

## Compatibility scope

- `bytebox` is the canonical import path.
- `memory_store` remains available as a temporary import shim for one transition release.
- The canonical CLI is `bytebox`.
- Legacy `MEMORY_STORE_` environment variables are not read at runtime anymore.
- `bytebox config migrate` translates legacy config files and env-style files into the ByteBox namespace without copying secret values.

## Removal policy

The `memory_store` shim should be removed only after:

1. `bytebox config migrate` exists.
2. Release notes document the deprecation window.
3. The next major release no longer promises the legacy import path.

Until then, avoid adding new public APIs that exist only under `memory_store`.