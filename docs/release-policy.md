# Release Policy

## Release source of truth

- Releases are built from a clean checkout.
- Versioned artifacts must be produced by CI, not committed from a developer workstation.
- `dist/`, `*.egg-info`, local databases, model caches, and pytest output captures do not belong
  in the repository.

## Release flow

1. Merge reviewed changes to `main`.
2. Ensure CI passes for the targeted release commit.
3. Build artifacts from CI.
4. Attach release notes and migration notes.
5. Publish only the artifacts produced from that CI run.

## Preview constraints

- Preview builds may change compatibility without notice except for the documented
  `memory_store` transition shim.
- Public releases must keep the provenance, notice, and model-license docs current.