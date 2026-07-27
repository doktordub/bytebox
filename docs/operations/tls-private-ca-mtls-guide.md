# TLS, Private CA, And mTLS Guide

## Inbound API TLS

ByteBox exposes API TLS settings in configuration, but the repository does not ship a dedicated ASGI process runner. Use the same certificate material in the host process or reverse proxy that terminates TLS.

```yaml
api:
  tls:
    enabled: true
    cert_file: ./tls/server.crt
    key_file: ./tls/server.key
    key_password: ${BYTEBOX_API__TLS__KEY_PASSWORD}
    client_ca_file: ./tls/client-ca.crt
    require_client_certificate: true
```

## Outbound provider TLS

```yaml
embeddings:
  remote:
    base_url: https://provider.internal:8443
    verify_tls: true
    ca_bundle_path: ./tls/private-ca.pem
    client_cert_path: ./tls/client.crt
    client_key_path: ./tls/client.key
    client_key_password: ${BYTEBOX_EMBEDDINGS__REMOTE__CLIENT_KEY_PASSWORD}
```

Apply the same pattern under `reranker.remote` when the reranker uses a remote endpoint.

## Private CA checklist

- install the private CA bundle on the ByteBox host only when required;
- prefer a dedicated CA bundle path over globally weakening TLS verification;
- rotate certificates and key passwords independently from API tokens;
- keep key passwords in environment variables or a secret manager, not in versioned YAML.

## mTLS checklist

- enable `require_client_certificate` only when the deployment path can present and verify client certificates end-to-end;
- provide a dedicated client CA file for inbound API mTLS;
- for outbound provider mTLS, configure both the client certificate and private key paths;
- verify that hostname validation still succeeds when the private CA is trusted.

## Acceptance tests

- approved certificate and hostname: request succeeds;
- unknown CA: startup or provider call fails safely;
- missing client certificate when required: handshake fails;
- wrong hostname: verification fails;
- secrets never appear in logs or API error payloads.