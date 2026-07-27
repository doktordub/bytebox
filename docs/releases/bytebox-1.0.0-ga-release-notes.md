# ByteBox 1.0.0 GA Release Notes

## Highlights

- canonical `bytebox` package, CLI, and config namespace
- configuration migration with `bytebox config migrate`
- database inspection, migration, verification, backup, restore, and re-embedding commands
- offline FastEmbed model management and manifest verification
- operational guides for remote providers, TLS, backup/restore, performance, and hardening

## Migration notes

- `memory_store` remains a temporary import shim for one transition release.
- legacy `MEMORY_STORE_` environment variables are no longer read at runtime.
- use `bytebox config migrate` before production cutover.
- use `bytebox database migrate` to create a new ByteBox-targeted copy instead of mutating the legacy database in place.

## Operator notes

- keep a verified backup until the release window closes;
- verify representative searches before switching writes;
- if the embedding identity changes, run `bytebox database reembed` before the final cutover;
- keep docs endpoints disabled or protected in production.

## Known limitations

- ArcadeDB Embedded remains a single-process embedded deployment model.
- remote-provider and soak-test execution still require operator validation in the release environment.
- the `memory_store` compatibility shim will be removed only according to the published migration policy.