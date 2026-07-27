# Ollama And llama.cpp Guide

## Scope

ByteBox supports remote embedding providers over controlled HTTP or HTTPS endpoints. Configure provider URLs statically at startup; do not accept provider URLs from API requests.

## Ollama embeddings

```yaml
embeddings:
  provider: ollama
  model: nomic-embed-text
  dim: 768
  remote:
    base_url: http://ollama.internal:11434
    allowed_hosts:
      - ollama.internal
    verify_tls: false
    trust_env: false
```

## llama.cpp embeddings and reranking

```yaml
embeddings:
  provider: llamacpp
  model: text-embedding
  dim: 768
  remote:
    base_url: https://llamacpp.internal:8081
    allowed_hosts:
      - llamacpp.internal
    verify_tls: true
    ca_bundle_path: ./tls/private-ca.pem

reranker:
  enabled: true
  provider: llamacpp
  model: bge-reranker
  remote:
    base_url: https://llamacpp.internal:8081
    allowed_hosts:
      - llamacpp.internal
    verify_tls: true
    ca_bundle_path: ./tls/private-ca.pem
```

## Operational notes

- Keep `trust_env` disabled unless you intentionally rely on proxy environment variables.
- Use `allowed_hosts` or `allowed_cidrs` to enforce the outbound destination policy.
- Keep redirects disabled unless a reviewed deployment requires them.
- Enable TLS verification for any network path outside a local-only lab environment.

## Acceptance checks

1. Start ByteBox with the provider configuration.
2. Confirm `/health/ready` is ready.
3. Run a representative search against stored data.

```powershell
bytebox database verify --config .\bytebox.yaml --database-path .\data\bytebox --search-query "deployment architecture"
```

4. Confirm provider failures return safe error codes rather than raw upstream payloads.