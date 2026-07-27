# GA Rollback Plan

## Objective

Restore the last approved ByteBox release and verified database copy without losing the ability to inspect the failed rollout.

## Inputs

- the last known-good release artifact
- the last verified backup path
- the last approved config and secrets
- the release-candidate soak report and go/no-go checklist

## Procedure

1. Stop the failed rollout.
2. Preserve the failed database copy and logs.
3. Restore the verified backup.
4. Restart the previous approved release.
5. Run readiness and database verification checks.
6. Reopen traffic only after representative searches pass.

## Verification

- `/health/ready` returns `200`;
- `bytebox database verify` reports `ok: true`;
- representative searches match the expected results;
- provider TLS and model identity checks match the prior approved release.

## Exit criteria

- the service is stable on the rolled-back release;
- the failed rollout remains available for diagnosis;
- the follow-up corrective work is tracked before the next release attempt.