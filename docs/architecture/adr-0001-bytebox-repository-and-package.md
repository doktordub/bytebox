# ADR 0001: ByteBox Repository And Package Strategy

- Status: Accepted
- Date: 2026-07-26

## Context

The current codebase is still branded as `memory-store` / `memory_store` and was cloned from `doktordub/mem-store`. The ByteBox plan explicitly rejects a blind global rename because the current repository already combines package identity, CLI naming, API naming, config namespaces, and migration-sensitive storage behavior.

Phase 0 needs a stable decision on how ByteBox will separate baseline preservation from product rebranding.

## Decision

ByteBox will proceed as a clean rebrand with a new package and repository identity rather than as an in-place, indefinite alias of `memory-store`.

The concrete Phase 1 direction is:

1. Keep the Phase 0 baseline artifacts in the current repository so the original system behavior remains measurable.
2. Move the product-facing package identity to `bytebox` and the product-facing CLI to `bytebox`.
3. Treat `memory_store` compatibility, if provided at all, as a temporary transition shim with an explicit removal policy.
4. Avoid a global search-and-replace rename that would blur baseline behavior and migration work.

## Consequences

- Packaging, docs, environment variables, and API titles will all change together in a controlled phase.
- Migration tooling is required because the product identity changes more than one surface.
- The baseline docs created in Phase 0 become the compatibility reference for future refactor work.
- Public release work must add explicit license and notice files before the rebrand is published.