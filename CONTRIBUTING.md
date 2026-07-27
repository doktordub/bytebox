# Contributing

## Development setup

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

## Recommended local workflow

```powershell
pre-commit install
pre-commit run --all-files
```

## Focused validation commands

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest tests/test_phase1_config_models.py
.\.venv\Scripts\python.exe -m pytest tests/test_phase9_quality_security.py
.\.venv\Scripts\python.exe -m pytest tests/test_imports.py -k "not health_smoke"
.\.venv\Scripts\python.exe -m ruff check src/bytebox/config.py src/bytebox/ingest_security.py src/bytebox/observability/redaction.py src/memory_store/__init__.py scripts/run_coverage_matrix.py tests/test_phase1_config_models.py tests/test_phase9_quality_security.py
.\.venv\Scripts\python.exe -m ruff format --check src/bytebox/config.py src/bytebox/ingest_security.py src/bytebox/observability/redaction.py src/memory_store/__init__.py scripts/run_coverage_matrix.py tests/test_phase1_config_models.py tests/test_phase9_quality_security.py
.\.venv\Scripts\python.exe -m mypy src/bytebox/config.py src/bytebox/ingest_security.py src/bytebox/observability/redaction.py src/memory_store/__init__.py scripts/run_coverage_matrix.py
.\.venv\Scripts\python.exe scripts/run_coverage_matrix.py
```

## Contribution rules

- Keep Phase 1 compatibility changes small and explicit.
- Treat `bytebox` as the canonical package and CLI surface.
- Use `memory_store` only for the documented transition shim.
- Do not commit generated `dist/`, `*.egg-info`, local databases, model files, test-output
  captures, secrets, or editor state.
- Update docs when the public package name, CLI, config shape, or migration policy changes.