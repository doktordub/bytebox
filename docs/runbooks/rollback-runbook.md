# Rollback Runbook

## When to roll back

- database verification fails after a migration;
- representative searches regress outside the approved threshold;
- provider TLS or identity changes break startup or readiness;
- a release introduces a security regression that cannot be hotfixed safely.

## Preconditions

- a verified backup exists;
- the target rollback path is known;
- operators have the previous approved config and secret set.

## Rollback steps

1. Stop ByteBox writes.
2. Restore the last known-good backup.

```powershell
bytebox database restore .\backups\bytebox-last-good --database-path .\data\bytebox --overwrite
```

3. Re-run verification.

```powershell
bytebox database verify --config .\bytebox.yaml --database-path .\data\bytebox --search-query "service layer"
```

4. Restart the previous approved ByteBox release.
5. Confirm readiness, status, and representative searches before reopening traffic.

## After rollback

- preserve the failed release artifacts and database copy for analysis;
- document the rollback trigger and operator decision;
- block further rollout until the issue is corrected and re-qualified.