# ByteBox Phase 0 Test And Coverage Report

Snapshot date: 2026-07-26

## Environment

- OS: Windows 11 10.0.26200, AMD64
- Python: CPython 3.13.12
- `memory-store`: 0.1.0
- `arcadedb-embedded`: 26.7.2
- `fastembed`: 0.8.0
- `pytest`: 9.1.1
- `coverage`: 7.15.2

## Commands Used

- `./.venv/Scripts/python.exe -m pytest --collect-only -q`
- `./.venv/Scripts/python.exe -m pytest -q`
- isolated per-file `pytest -q` invocations with native-crash signature detection
- isolated per-file `coverage run --branch --parallel-mode -m pytest -q <file>`
- `./.venv/Scripts/python.exe -m coverage combine`
- `./.venv/Scripts/python.exe -m coverage report -m`

## Baseline Outcome

- Collected tests: `93`
- Full-suite `pytest -q` result: `exit code 1`
- Full-suite failure mode: repeated `Windows fatal exception: access violation` traces from `jpype` / `arcadedb_embedded` while opening or using ArcadeDB Embedded.
- Isolated test files all returned `exit code 0`, but many still emitted the native crash signature to stderr.
- Conclusion: the current suite is not cleanly stable on the measured Windows 11 + CPython 3.13.12 baseline. The defects appear native-runtime related rather than assertion-driven.

## Isolated Test Matrix

| Test file | Tests | Isolated exit code | Native crash signature |
|---|---:|---:|---|
| `tests/test_imports.py` | 17 | 0 | yes |
| `tests/test_phase1_config_models.py` | 9 | 0 | no |
| `tests/test_phase10_rest_cli.py` | 5 | 0 | yes |
| `tests/test_phase11_evals_docs.py` | 6 | 0 | yes |
| `tests/test_phase2_arcade_persistence.py` | 12 | 0 | yes |
| `tests/test_phase3_service_api.py` | 1 | 0 | yes |
| `tests/test_phase4_embeddings.py` | 1 | 0 | no |
| `tests/test_phase4_service_api.py` | 5 | 0 | yes |
| `tests/test_phase5_lifecycle.py` | 3 | 0 | yes |
| `tests/test_phase6_ingest_orchestration.py` | 5 | 0 | no |
| `tests/test_phase6_markdown_ingestion.py` | 16 | 0 | yes |
| `tests/test_phase7_hybrid_retrieval.py` | 7 | 0 | yes |
| `tests/test_phase8_scoring_diagnostics.py` | 3 | 0 | yes |
| `tests/test_phase9_privacy_controls.py` | 3 | 0 | yes |

Interpretation:

- `phase1`, `phase4_embeddings`, and `phase6_ingest_orchestration` were the only files that ran without the native crash signature.
- Every other file exercised code paths that caused native-access-violation traces even though the isolated process exit code remained `0`.
- This distinction matters: current regressions should be compared against the isolated-file baseline, not against a naive assumption that the suite is clean on Windows.

## Branch-Aware Coverage Baseline

Coverage was collected by running each test file separately in `coverage --parallel-mode`, then combining the results. This avoids relying on a single monolithic pytest process, which is currently unstable on the measured Windows runtime.

Overall combined coverage from `coverage report -m`:

- Combined statement-and-branch coverage: `87%`

Package summary:

| Package | Statements | Statement coverage | Branches | Branch coverage | Combined coverage |
|---|---:|---:|---:|---:|---:|
| `memory_store` | 3742 | 88.51% | 1062 | 71.28% | 84.70% |
| `evals` | 337 | 91.69% | 68 | 64.71% | 87.16% |
| `examples` | 113 | 92.92% | 14 | 50.00% | 88.19% |
| `tests` | 1774 | 91.66% | 56 | 71.43% | 91.04% |

Notable low-coverage implementation areas from the file report:

- `memory_store/arcade/transactions.py`: 59%
- `memory_store/arcade/connection.py`: 66%
- `memory_store/embeddings/fastembed_provider.py`: 71%
- `memory_store/retrieval/graph.py`: 73%
- `memory_store/api/schemas.py`: 77%
- `memory_store/cli.py`: 77%
- `memory_store/service.py`: 79%

## Baseline Defects Separated From Future Refactor Regressions

The following are known pre-refactor conditions and should not be misclassified as ByteBox regressions later:

1. The full-suite Windows baseline exits non-zero even though isolated files can return `0`.
2. ArcadeDB Embedded via JPype emits `Windows fatal exception: access violation` during multiple tests.
3. Coverage can be produced only by isolated-file runs on the measured Windows baseline.
4. The repository does not yet enforce coverage in CI even though the measured combined result is already above the future 85% target.

## Gate Result

Phase 0 testing and coverage measurement is complete.

- Current defects are now documented.
- A reproducible measurement method exists for Windows.
- The repository has evidence for a future `85%` branch-aware gate, but the gate is not yet enforced in tooling.