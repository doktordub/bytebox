# Go/No-Go Checklist

## Release metadata

- release: `bytebox-1.0.0-rc1`
- release date: `<fill in>`
- approver: `<fill in>`
- final decision: `<go | no-go>`

## Checklist

- [ ] `bytebox config migrate` dry-run and write paths were exercised against representative legacy input.
- [ ] `bytebox database migrate --dry-run` was reviewed before the cutover run.
- [ ] a verified backup exists for the candidate release.
- [ ] `bytebox database verify` passed against the migrated copy.
- [ ] re-embedding was either not needed or completed successfully.
- [ ] offline FastEmbed or approved remote-provider deployment passed acceptance checks.
- [ ] startup, readiness, status, and metrics were reviewed in the target environment.
- [ ] dependency audit, secret scan, static analysis, and SBOM generation passed.
- [ ] the release-candidate soak report is complete.
- [ ] the rollback runbook was rehearsed against a verified backup.

## Sign-off

- engineering: `<fill in>`
- security: `<fill in>`
- operations: `<fill in>`

If any item remains unchecked, the candidate is `no-go` until the risk is explicitly accepted.