# Health, Metrics, And Logging Guide

## Health endpoints

| Endpoint | Purpose | Notes |
|---|---|---|
| `/health/live` | Process liveness | No deep dependency calls |
| `/health/ready` | Admission control | Returns `503` when required dependencies are not ready |
| `/status` | Safe runtime summary | Build, schema, provider, TLS, and logging state |
| `/state` | Privileged operations view | Counters, circuit state, safe recent errors |
| `/metrics` | Monitoring scrape | Enable and protect separately as needed |

## Trace IDs

Every request should return `X-Trace-ID`. Use that value to correlate API failures with structured logs and remote provider calls.

## Logging levels

```yaml
logging:
  level: info
  format: json
```

Supported levels are `debug`, `info`, `warn`, and `off`.

- `debug` adds safe operational detail.
- `info` records normal lifecycle and request outcomes.
- `warn` records degraded behavior and recoverable failures.
- `off` suppresses ByteBox application logs and access logs.

## Safe operational expectations

- logs must not contain memory text, embeddings, API tokens, TLS key material, or raw provider response bodies;
- status and state must not expose absolute filesystem paths or secrets;
- metrics should remain aggregate-only and content-free.

## Recommended checks

```powershell
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
curl http://127.0.0.1:8080/status -H "X-API-Token: <operator-token>"
```

Use the returned trace ID when investigating any non-200 response.