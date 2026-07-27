"""Filesystem safeguards for ByteBox ingestion workflows."""

from __future__ import annotations

import os
import re
import stat
from hashlib import sha256
from pathlib import Path

from .errors import IngestionError

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def resolve_ingest_path(
    path: str | Path,
    *,
    ingest_roots: list[Path],
    allow_symlinks: bool,
    expect_directory: bool,
) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        noun = "folder" if expect_directory else "file"
        raise IngestionError(f"Markdown {noun} was not found: {candidate}") from exc

    if not allow_symlinks and _path_uses_symlink(candidate):
        raise IngestionError(f"Symlinks are not allowed for ingestion: {candidate}")

    if ingest_roots:
        resolved_roots = [root.expanduser().resolve(strict=False) for root in ingest_roots]
        if not any(resolved.is_relative_to(root) for root in resolved_roots):
            raise IngestionError(f"Ingest path is outside configured roots: {candidate}")

    mode = resolved.stat().st_mode
    if expect_directory:
        if not stat.S_ISDIR(mode):
            raise IngestionError(f"Ingest path must be a directory: {candidate}")
    elif not stat.S_ISREG(mode):
        raise IngestionError(f"Ingest path must be a regular file: {candidate}")

    return resolved


def collect_markdown_files(root: Path, *, allow_symlinks: bool) -> list[Path]:
    queue = [root]
    seen_directories = {root.resolve(strict=True).as_posix()}
    markdown_files: list[Path] = []

    while queue:
        current = queue.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.as_posix())
        except OSError as exc:
            raise IngestionError(f"Failed to enumerate Markdown folder {current}: {exc}") from exc

        for child in children:
            if child.is_symlink() and not allow_symlinks:
                raise IngestionError(f"Symlinks are not allowed for ingestion: {child}")

            try:
                resolved_child = child.resolve(strict=True)
            except OSError as exc:
                raise IngestionError(f"Failed to inspect ingestion path {child}: {exc}") from exc

            if resolved_child.is_dir():
                key = resolved_child.as_posix()
                if key not in seen_directories:
                    seen_directories.add(key)
                    queue.append(resolved_child)
                continue

            if resolved_child.is_file() and resolved_child.suffix.lower() in _MARKDOWN_SUFFIXES:
                markdown_files.append(resolved_child)
                continue

            if resolved_child.exists() and not resolved_child.is_file():
                raise IngestionError(f"Unsupported ingestion path type encountered: {child}")

    return sorted(markdown_files)


def default_ingest_manifest_path(root: Path, *, state_dir: Path) -> Path:
    safe_name = _SAFE_NAME_RE.sub("-", root.name or "root").strip(".-") or "root"
    digest = sha256(root.resolve(strict=False).as_posix().encode("utf-8")).hexdigest()[:16]
    return state_dir.expanduser() / "ingest-manifests" / f"{safe_name}-{digest}.json"


def _path_uses_symlink(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor) if absolute.anchor else Path(".")
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts

    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return False
    return False
