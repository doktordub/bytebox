# Offline Model Provisioning Guide

## Goal

Provision FastEmbed model assets locally and run ByteBox without runtime Hugging Face downloads.

## Required settings

```yaml
embeddings:
  provider: fastembed
  model: BAAI/bge-small-en-v1.5
  model_path: ./models/bge-small-en-v1.5
  local_files_only: true
  hf_hub_offline: true
  require_manifest: true
  require_checksums: true

reranker:
  enabled: true
  provider: fastembed
  model: Xenova/ms-marco-MiniLM-L-6-v2
  model_path: ./models/ms-marco-MiniLM-L-6-v2
  local_files_only: true
  hf_hub_offline: true
  require_manifest: true
  require_checksums: true
```

## Install local model assets

```powershell
bytebox models install --source .\artifacts\bge-small-en-v1.5
bytebox models install --capability reranker --source .\artifacts\ms-marco-MiniLM-L-6-v2
```

## Verify manifests and checksums

```powershell
bytebox models list
bytebox models inspect
bytebox models verify
bytebox models doctor
```

## Acceptance checks

- `strict_offline` is `true` in `bytebox models list`.
- `verification.ok` is `true` for every configured capability.
- startup succeeds with outbound network access blocked.
- the manifest records model identity, revision or digest, and file checksums.

## Failure handling

- If `models verify` fails, replace the local artifact rather than disabling checksum validation.
- If startup reports a missing manifest, export or regenerate it before production use.
- If embedding identity changes, run `bytebox database reembed` before cutover.