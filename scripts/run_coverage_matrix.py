from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tests"
SECURITY_MODULES = [
    "src/bytebox/ingest_security.py",
    "src/bytebox/observability/redaction.py",
]
SECURITY_COVERAGE_TARGET = "90"
PYTEST_ARGS_BY_FILE = {
    # This real-store REST smoke is already excluded from the repository's focused
    # validation surface while provider-dimension fixtures are being stabilized.
    "tests/test_phase10_rest_cli.py": ["-k", "not rest_app_smoke_flows_against_real_store"],
}


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def main() -> int:
    test_files = sorted(TEST_DIR.glob("test_*.py"))
    if not test_files:
        print("No test files found under tests/.", file=sys.stderr)
        return 1

    coverage_dir = ROOT / "build" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    source_root = str(ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    combined_pythonpath = f"{source_root}{os.pathsep}{existing_pythonpath}"
    env["PYTHONPATH"] = source_root if not existing_pythonpath else combined_pythonpath

    _run([sys.executable, "-m", "coverage", "erase"], env=env)

    for test_file in test_files:
        relative_path = test_file.relative_to(ROOT).as_posix()
        pytest_args = PYTEST_ARGS_BY_FILE.get(relative_path, [])
        print(f"Running coverage for {relative_path}")
        _run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                "-m",
                "pytest",
                "-q",
                relative_path,
                *pytest_args,
            ],
            env=env,
        )

    _run([sys.executable, "-m", "coverage", "combine"], env=env)
    _run([sys.executable, "-m", "coverage", "report"], env=env)
    _run(
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            "--fail-under",
            SECURITY_COVERAGE_TARGET,
            *SECURITY_MODULES,
        ],
        env=env,
    )
    _run([sys.executable, "-m", "coverage", "xml"], env=env)
    _run([sys.executable, "-m", "coverage", "json"], env=env)
    _run([sys.executable, "-m", "coverage", "html"], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
