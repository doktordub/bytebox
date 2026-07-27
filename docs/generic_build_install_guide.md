# ByteBox Package Build, Install, Reinstall, and Uninstall Guide

This guide documents the standard local Python packaging workflow for building and installing the `bytebox` package from source.

## 1. Recommended Project Structure

A minimal Python package should look similar to this:

```text
bytebox/
├── pyproject.toml
├── README.md
├── src/
│   └── bytebox/
│       ├── __init__.py
│       └── ...
└── tests/
    └── ...
```

Recommended package name conventions:

- **Distribution/package artifact name:** `bytebox`
- **Import/module name:** `bytebox`
- **Uninstall name:** usually the project name defined in `pyproject.toml`

Example import:

```python
import bytebox
```

---

## 2. Prerequisites

Use a virtual environment before building or installing locally.

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Upgrade packaging tools:

```bash
python -m pip install --upgrade pip setuptools wheel build
```

Or install only the `build` package if the other tools are already current:

```bash
pip install build
```

---

## 3. Build the Package

From the project root, run:

```bash
python -m build
```

This creates a `dist/` directory with both a source distribution and a wheel.

Expected result:

```text
dist/
├── bytebox-0.1.0.tar.gz
└── bytebox-0.1.0-py3-none-any.whl
```

### What These Files Mean

| File | Meaning | When to Use |
|---|---|---|
| `bytebox-0.1.0-py3-none-any.whl` | Built wheel package | Preferred for installation because it is faster and already built |
| `bytebox-0.1.0.tar.gz` | Source distribution | Useful for source-based installs or publishing source packages |

---

## 4. Clean Build Artifacts Before Rebuilding

When rebuilding frequently, clean old generated artifacts first:

```bash
rm -rf dist/ build/ *.egg-info src/*.egg-info
```

On Windows PowerShell:

```powershell
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter "*.egg-info" | Remove-Item -Recurse -Force
```

Then rebuild:

```bash
python -m build
```

---

## 5. Install the Built Package

### Preferred: Install from Wheel

```bash
pip install dist/bytebox-0.1.0-py3-none-any.whl
```

Or, if you are already inside the `dist/` directory:

```bash
pip install memory_store-0.1.0-py3-none-any.whl
```

### Install from Source Distribution

```bash
pip install dist/memory_store-0.1.0.tar.gz
```

Or, if you are already inside the `dist/` directory:

```bash
pip install memory_store-0.1.0.tar.gz
```

---

## 6. Verify the Installation

Check that the package is installed:

```bash
pip show memory_store
```

Verify that Python can import it:

```bash
python -c "import memory_store; print(memory_store.__name__)"
```

If the package exposes a version in `memory_store.__version__`, verify it with:

```bash
python -c "import memory_store; print(memory_store.__version__)"
```

---

## 7. Reinstall the Package

Use reinstall when you have rebuilt the package and want to replace the currently installed version.

### Reinstall from Wheel

```bash
pip install --force-reinstall dist/memory_store-0.1.0-py3-none-any.whl
```

Or, from inside the `dist/` directory:

```bash
pip install --force-reinstall memory_store-0.1.0-py3-none-any.whl
```

### Reinstall from Source Distribution

```bash
pip install --force-reinstall dist/memory_store-0.1.0.tar.gz
```

Or, from inside the `dist/` directory:

```bash
pip install --force-reinstall memory_store-0.1.0.tar.gz
```

### Recommended Rebuild + Reinstall Workflow

```bash
rm -rf dist/ build/ *.egg-info src/*.egg-info
python -m build
pip install --force-reinstall dist/memory_store-0.1.0-py3-none-any.whl
```

---

## 8. Editable Install for Development

During active development, an editable install is often easier than rebuilding after every code change.

From the project root:

```bash
pip install -e .
```

Use editable mode when:

- You are actively changing package code.
- You want local code changes to be reflected immediately.
- You do not need to test the final packaged wheel yet.

Use `python -m build` when:

- You are testing the real distributable package.
- You are preparing a release.
- You want to verify the final wheel or source distribution.

---

## 9. Uninstall the Package

```bash
pip uninstall memory_store
```

If your `pyproject.toml` uses a different distribution name, uninstall using that name instead.

Example:

```bash
pip uninstall bytebox
```

To confirm removal:

```bash
pip show memory_store
```

If the package is removed, `pip show` should return no package information.

---

## 10. Common Troubleshooting

### Problem: `python -m build` fails because `build` is missing

Install build:

```bash
pip install build
```

Then run:

```bash
python -m build
```

### Problem: Old package behavior still appears after reinstall

Clean artifacts, rebuild, and force reinstall:

```bash
rm -rf dist/ build/ *.egg-info src/*.egg-info
python -m build
pip install --force-reinstall dist/memory_store-0.1.0-py3-none-any.whl
```

Also confirm you are using the correct Python environment:

```bash
which python
which pip
python -m pip --version
```

On Windows PowerShell:

```powershell
where python
where pip
python -m pip --version
```

### Problem: Package installs but import fails

Check that the module directory exists and contains `__init__.py`:

```text
src/memory_store/__init__.py
```

Also confirm that `pyproject.toml` is configured to include packages from `src/`.

### Problem: You installed with `pip`, but it went to the wrong Python

Prefer this form because it binds `pip` to the active Python interpreter:

```bash
python -m pip install dist/memory_store-0.1.0-py3-none-any.whl
```

---

## 11. Full Command Summary

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Upgrade packaging tools
python -m pip install --upgrade pip setuptools wheel build

# Clean old build artifacts
rm -rf dist/ build/ *.egg-info src/*.egg-info

# Build package
python -m build

# Install from wheel
python -m pip install dist/memory_store-0.1.0-py3-none-any.whl

# Verify install
python -m pip show memory_store
python -c "import memory_store; print(memory_store.__name__)"

# Force reinstall from wheel
python -m pip install --force-reinstall dist/memory_store-0.1.0-py3-none-any.whl

# Force reinstall from source distribution
python -m pip install --force-reinstall dist/memory_store-0.1.0.tar.gz

# Uninstall
python -m pip uninstall memory_store
```

---

## 12. Recommended Release Checklist

Before sharing or publishing the package, verify:

- `python -m build` completes successfully.
- `dist/` contains both `.whl` and `.tar.gz` files.
- The wheel installs cleanly in a fresh virtual environment.
- `import memory_store` works.
- Package version is correct.
- Required dependencies are listed in `pyproject.toml`.
- Development-only dependencies are separated from runtime dependencies.
- README and license files are included if the package will be distributed.
