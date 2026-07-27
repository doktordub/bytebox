from __future__ import annotations

import os
import string
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, SecretStr

from bytebox.config import load_settings
from bytebox.errors import IngestionError
from bytebox.ingest_security import (
    collect_markdown_files,
    default_ingest_manifest_path,
    resolve_ingest_path,
)
from bytebox.observability.redaction import Redactor

pytestmark = [pytest.mark.security, pytest.mark.property]

_SAFE_NAME_CHARS = string.ascii_letters + string.digits + "._- "
_SAFE_FILE_CHARS = string.ascii_letters + string.digits + "_-"
_TOKEN_CHARS = string.ascii_letters + string.digits + "._-"


def _safe_name_strategy(*, min_size: int = 1, max_size: int = 24) -> st.SearchStrategy[str]:
    return st.text(alphabet=_SAFE_NAME_CHARS, min_size=min_size, max_size=max_size).filter(
        lambda value: value.strip(" .-") != ""
    )


def _safe_file_stem_strategy() -> st.SearchStrategy[str]:
    return st.text(alphabet=_SAFE_FILE_CHARS, min_size=1, max_size=16).filter(
        lambda value: value not in {".", ".."}
    )


@given(secret=st.text(alphabet=_TOKEN_CHARS, min_size=1, max_size=32))
def test_redactor_never_leaks_sensitive_header_values(secret: str) -> None:
    redactor = Redactor()
    sanitized = redactor.sanitize_headers(
        {
            "Authorization": f"Bearer {secret}",
            "X-Api-Token": secret,
        }
    )

    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["X-Api-Token"] == "[REDACTED]"


@given(secret=st.text(alphabet=_TOKEN_CHARS, min_size=1, max_size=32))
def test_redactor_masks_sensitive_field_names(secret: str) -> None:
    redactor = Redactor()
    payload = {
        "api_token": secret,
        "nested": {
            "password": secret,
        },
    }

    sanitized = redactor.redact(payload)

    assert sanitized["api_token"] == "[REDACTED]"
    assert sanitized["nested"]["password"] == "[REDACTED]"


@given(root_name=_safe_name_strategy())
def test_default_ingest_manifest_path_stays_under_state_dir(root_name: str) -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        state_dir = temp_path / "state"
        root = temp_path / root_name

        manifest_path = default_ingest_manifest_path(root, state_dir=state_dir)

        assert manifest_path.parent == state_dir / "ingest-manifests"
        assert manifest_path.suffix == ".json"
        assert manifest_path.name == manifest_path.name.strip(".-")
        assert all(
            character in string.hexdigits for character in manifest_path.stem.rsplit("-", 1)[-1]
        )


@given(file_stem=_safe_file_stem_strategy())
def test_resolve_ingest_path_accepts_files_inside_configured_roots(file_stem: str) -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        root = temp_path / "ingest-root"
        root.mkdir()
        target = root / f"{file_stem}.md"
        target.write_text("# inside\n", encoding="utf-8")

        resolved = resolve_ingest_path(
            target,
            ingest_roots=[root],
            allow_symlinks=False,
            expect_directory=False,
        )

        assert resolved == target.resolve(strict=True)


@given(file_stem=_safe_file_stem_strategy())
def test_resolve_ingest_path_rejects_files_outside_configured_roots(file_stem: str) -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        root = temp_path / "ingest-root"
        root.mkdir()
        outside = temp_path / f"{file_stem}.md"
        outside.write_text("# outside\n", encoding="utf-8")

        with pytest.raises(IngestionError):
            resolve_ingest_path(
                outside,
                ingest_roots=[root],
                allow_symlinks=False,
                expect_directory=False,
            )


@given(
    yaml_port=st.integers(min_value=1025, max_value=65535),
    env_port=st.integers(min_value=1025, max_value=65535),
    override_port=st.integers(min_value=1025, max_value=65535),
)
def test_load_settings_preserves_override_precedence(
    yaml_port: int,
    env_port: int,
    override_port: int,
) -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "bytebox.yaml"
        config_path.write_text(f"api:\n  port: {yaml_port}\n", encoding="utf-8")

        with patch.dict(os.environ, {"BYTEBOX_API__PORT": str(env_port)}, clear=False):
            merged = load_settings(config_path, api={"port": override_port})
            env_overrides_yaml = load_settings(config_path)

        assert merged.api.port == override_port
        assert env_overrides_yaml.api.port == env_port


def test_collect_markdown_files_discovers_nested_markdown_only(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    nested = root / "nested"
    root.mkdir()
    nested.mkdir()
    markdown_file = nested / "guide.md"
    markdown_file.write_text("# Guide\n", encoding="utf-8")
    (root / "ignore.txt").write_text("skip\n", encoding="utf-8")

    discovered = collect_markdown_files(root, allow_symlinks=False)

    assert discovered == [markdown_file.resolve(strict=True)]


def test_resolve_ingest_path_reports_missing_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(IngestionError, match="Markdown file was not found"):
        resolve_ingest_path(
            missing,
            ingest_roots=[tmp_path],
            allow_symlinks=False,
            expect_directory=False,
        )


def test_resolve_ingest_path_rejects_wrong_path_kinds(tmp_path: Path) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    file_path = tmp_path / "note.md"
    file_path.write_text("# note\n", encoding="utf-8")

    with pytest.raises(IngestionError, match="must be a regular file"):
        resolve_ingest_path(
            directory,
            ingest_roots=[tmp_path],
            allow_symlinks=False,
            expect_directory=False,
        )

    with pytest.raises(IngestionError, match="must be a directory"):
        resolve_ingest_path(
            file_path,
            ingest_roots=[tmp_path],
            allow_symlinks=False,
            expect_directory=True,
        )


def test_resolve_ingest_path_rejects_symlinked_paths(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("# note\n", encoding="utf-8")

    with patch("bytebox.ingest_security._path_uses_symlink", return_value=True):
        with pytest.raises(IngestionError, match="Symlinks are not allowed"):
            resolve_ingest_path(
                target,
                ingest_roots=[tmp_path],
                allow_symlinks=False,
                expect_directory=False,
            )


def test_collect_markdown_files_wraps_directory_enumeration_errors(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    original_iterdir = Path.iterdir

    def failing_iterdir(self: Path):
        if self == root:
            raise OSError("boom")
        return original_iterdir(self)

    with patch.object(Path, "iterdir", failing_iterdir):
        with pytest.raises(IngestionError, match="Failed to enumerate Markdown folder"):
            collect_markdown_files(root, allow_symlinks=False)


def test_collect_markdown_files_rejects_symlink_children(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    child = Mock()
    child.as_posix.return_value = "docs/link"
    child.is_symlink.return_value = True

    with patch.object(Path, "iterdir", return_value=[child]):
        with pytest.raises(IngestionError, match="Symlinks are not allowed"):
            collect_markdown_files(root, allow_symlinks=False)


def test_collect_markdown_files_wraps_child_resolution_failures(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    child = Mock()
    child.as_posix.return_value = "docs/broken.md"
    child.is_symlink.return_value = False
    child.resolve.side_effect = OSError("broken")

    with patch.object(Path, "iterdir", return_value=[child]):
        with pytest.raises(IngestionError, match="Failed to inspect ingestion path"):
            collect_markdown_files(root, allow_symlinks=False)


def test_redactor_handles_scalars_paths_secrets_exceptions_and_models() -> None:
    class ExampleModel(BaseModel):
        token: str
        count: int

    class ExampleError(RuntimeError):
        def __init__(self, code: str) -> None:
            super().__init__("boom")
            self.code = code

    redactor = Redactor(max_field_length=12)

    assert redactor.redact(None) is None
    assert redactor.redact(True) is True
    assert redactor.redact(3.14) == pytest.approx(3.14)
    assert redactor.redact(SecretStr("secret-value")) == "[REDACTED]"
    assert redactor.redact(Path("C:/tmp/secrets.txt")) == "secrets.txt"
    assert redactor.redact(ExampleError("token=abc123")) == {
        "type": "ExampleError",
        "code": "[REDACTED]",
    }
    assert redactor.redact(ExampleModel(token="secret-value", count=2)) == {
        "token": "[REDACTED]",
        "count": 2,
    }


def test_redactor_sanitizes_text_sequences_headers_and_safe_exceptions() -> None:
    redactor = Redactor(max_field_length=18)

    sanitized_text = redactor.redact(
        "Bearer abc123\npassword=hunter2\r\nsk-abcdef",
        field_name="x-request-id",
    )
    assert sanitized_text.endswith("...[truncated]")
    assert "Bearer" not in sanitized_text
    assert "hunter2" not in sanitized_text
    assert "sk-abcdef" not in sanitized_text
    assert "\\n" in sanitized_text

    sanitized_sequence = redactor.redact(["token=abc", {"note": "safe"}])
    assert sanitized_sequence == ["[REDACTED]", {"note": "safe"}]

    sanitized_headers = redactor.sanitize_headers(
        {
            "X-Request-ID": "line1\nline2",
            "Authorization": "Bearer abc123",
        }
    )
    assert sanitized_headers == {
        "X-Request-ID": "line1\\nline2",
        "Authorization": "[REDACTED]",
    }
    assert redactor.sanitize_headers(None) == {}
    assert redactor.safe_exception(RuntimeError("boom")) == {
        "type": "RuntimeError",
        "code": None,
    }
