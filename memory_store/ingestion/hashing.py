"""Stable hashing helpers for deterministic markdown ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path


def normalize_source_path(source_path: str | Path) -> str:
	path = Path(source_path)
	if path.is_absolute():
		return path.resolve().as_posix()
	return path.as_posix()


def compute_source_hash(
	source_path: str | Path,
	heading_path: Sequence[str] | None,
	chunk_index: int,
) -> str:
	return _hash_text(
		"\n".join(
			[
				normalize_source_path(source_path),
				_heading_path_key(heading_path),
				str(chunk_index),
			]
		)
	)


def compute_content_hash(content: str) -> str:
	return _hash_text(content.replace("\r\n", "\n").replace("\r", "\n"))


def compute_chunk_id(
	source_path: str | Path,
	heading_path: Sequence[str] | None,
	chunk_index: int,
	content_hash: str,
) -> str:
	return _hash_text(
		"\n".join(
			[
				normalize_source_path(source_path),
				_heading_path_key(heading_path),
				str(chunk_index),
				content_hash,
			]
		)
	)


def _heading_path_key(heading_path: Sequence[str] | None) -> str:
	return " > ".join(str(part) for part in (heading_path or ()))


def _hash_text(value: str) -> str:
	return sha256(value.encode("utf-8")).hexdigest()
