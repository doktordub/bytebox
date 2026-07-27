from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "bytebox"
POLICY_DOC = REPO_ROOT / "docs" / "architecture" / "module-size-policy.md"

EXPECTED_PHASE2_PATHS = [
    SRC_ROOT / "application" / "__init__.py",
    SRC_ROOT / "application" / "ports.py",
    SRC_ROOT / "domain" / "__init__.py",
    SRC_ROOT / "domain" / "base.py",
    SRC_ROOT / "domain" / "memory.py",
    SRC_ROOT / "domain" / "retrieval.py",
    SRC_ROOT / "domain" / "ingestion.py",
    SRC_ROOT / "domain" / "privacy.py",
    SRC_ROOT / "domain" / "administration.py",
    SRC_ROOT / "services" / "commands.py",
    SRC_ROOT / "services" / "queries.py",
    SRC_ROOT / "services" / "retrieval.py",
    SRC_ROOT / "services" / "ingestion.py",
    SRC_ROOT / "services" / "ingestion_document.py",
    SRC_ROOT / "services" / "ingestion_folder.py",
    SRC_ROOT / "services" / "lifecycle.py",
    SRC_ROOT / "services" / "privacy.py",
    SRC_ROOT / "services" / "administration.py",
    SRC_ROOT / "cli_handlers.py",
]

ARCHITECTURE_ROOTS = [
    SRC_ROOT / "application",
    SRC_ROOT / "domain",
    SRC_ROOT / "services",
]
FORBIDDEN_IMPORT_ROOTS = {
    "arcadedb_embedded",
    "fastapi",
    "fastembed",
    "httpx",
    "uvicorn",
}

PHASE2_SIZE_TARGETS = [
    SRC_ROOT / "application",
    SRC_ROOT / "domain",
    SRC_ROOT / "services",
    SRC_ROOT / "service.py",
    SRC_ROOT / "models.py",
    SRC_ROOT / "cli.py",
    SRC_ROOT / "cli_handlers.py",
]
DEFAULT_LINE_LIMIT = 400
LINE_LIMIT_EXCEPTIONS = {
    "src/bytebox/service.py": 650,
    "src/bytebox/services/ingestion_document.py": 650,
    "src/bytebox/services/ingestion_folder.py": 450,
}


def _iter_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_phase2_package_structure_exists() -> None:
    missing = [path.relative_to(REPO_ROOT).as_posix() for path in EXPECTED_PHASE2_PATHS if not path.exists()]
    assert missing == []


def test_phase2_layers_avoid_forbidden_external_imports() -> None:
    violations: list[str] = []

    for root in ARCHITECTURE_ROOTS:
        for path in _iter_python_files(root):
            for module in _iter_imports(path):
                if module.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(REPO_ROOT).as_posix()} imports {module}")

    assert violations == []


def test_phase2_module_size_policy_is_documented_and_enforced() -> None:
    assert POLICY_DOC.exists() is True

    policy_text = POLICY_DOC.read_text(encoding="utf-8")
    for exception_path in LINE_LIMIT_EXCEPTIONS:
        assert exception_path in policy_text

    violations: list[str] = []
    for target in PHASE2_SIZE_TARGETS:
        for path in _iter_python_files(target):
            relative = path.relative_to(REPO_ROOT).as_posix()
            limit = LINE_LIMIT_EXCEPTIONS.get(relative, DEFAULT_LINE_LIMIT)
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > limit:
                violations.append(f"{relative} has {line_count} lines (limit {limit})")

    assert violations == []