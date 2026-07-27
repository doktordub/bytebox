# Release Candidate Soak Report

## Status

Prepared on 2026-07-26 for external execution in the release environment.

## Candidate

- release: `bytebox-1.0.0-rc1`
- config revision: `<fill in>`
- database snapshot: `<fill in>`
- model manifests: `<fill in>`

## Dataset profile

- representative records: `<fill in>`
- document chunks: `<fill in>`
- embedding provider: `<fill in>`
- reranker provider: `<fill in>`

## Soak scenarios

| Scenario | Duration | Fault injection | Expected result | Observed result |
|---|---:|---|---|---|
| steady read traffic | 24h | none | readiness stays green | pending |
| bulk ingestion | 2h | none | bounded memory and no data loss | pending |
| provider timeout | 30m | delayed upstream responses | safe errors and recovery | pending |
| provider outage | 30m | hard refusal / DNS block | readiness reflects degradation | pending |
| TLS failure | 30m | invalid CA or hostname | startup or calls fail safely | pending |
| rollback rehearsal | 1h | restore from backup | verify and reopen traffic | pending |

## Evidence

- `bytebox database inspect` output: pending
- `bytebox database verify` output: pending
- representative eval report: pending
- incident notes: pending

Do not tag GA until every row above is complete and signed off in the go/no-go checklist.