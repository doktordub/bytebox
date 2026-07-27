# Incident Response Runbook

## Trigger conditions

- startup fails after a config or provider change;
- readiness flips to `503` unexpectedly;
- remote provider calls start timing out or returning malformed data;
- data corruption, secret exposure, or unauthorized deletion is suspected.

## First response

1. Stop new writes when data integrity is in doubt.
2. Record the release version, config revision, and the request `X-Trace-ID` values involved.
3. Capture `bytebox database inspect` output.
4. Create a fresh backup before any destructive recovery step.

## Triage commands

```powershell
bytebox database inspect --config .\bytebox.yaml
bytebox database verify --config .\bytebox.yaml --search-query "service layer"
bytebox models doctor --config .\bytebox.yaml
```

## Scenario guidance

### Startup or readiness failure

- verify the schema version and active embedding dimension;
- verify local model manifests and remote-provider TLS settings;
- revert the last config change before modifying data.

### Suspected secret exposure

- rotate API tokens and TLS key passwords;
- preserve logs and traces for review;
- verify that public status and state responses do not expose secret material.

### Suspected data corruption

- preserve the current database copy;
- restore a known-good backup to an isolated path;
- verify the restored copy before reopening traffic.

## Exit criteria

- the root cause is documented;
- the affected release is either fixed or rolled back;
- follow-up actions are filed for any missing guardrail revealed by the incident.