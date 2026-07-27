"""Helpers for building embedding input text."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import MemoryCreate, MemoryRecord


def build_embedding_text(
    memory: MemoryCreate | MemoryRecord,
    *,
    include_heading_path: bool = True,
    include_frontmatter: bool = True,
) -> str:
    parts: list[str] = []

    if memory.title:
        parts.append(f"Title: {memory.title}")
    if memory.summary:
        parts.append(f"Summary: {memory.summary}")
    if memory.tags:
        parts.append("Tags: " + ", ".join(memory.tags))

    source_fields: list[str] = []
    if memory.source_type is not None:
        source_fields.append(f"type={memory.source_type}")
    if memory.source_path:
        source_fields.append(f"path={memory.source_path}")
    if memory.source_uri:
        source_fields.append(f"uri={memory.source_uri}")
    if memory.source_hash:
        source_fields.append(f"hash={memory.source_hash}")
    if source_fields:
        parts.append("Source: " + "; ".join(source_fields))

    if include_heading_path and memory.heading_path:
        parts.append("Headings: " + " > ".join(memory.heading_path))

    if include_frontmatter:
        frontmatter = memory.metadata.get("frontmatter")
        if isinstance(frontmatter, Mapping) and frontmatter:
            serialized = _serialize_mapping(frontmatter)
            if serialized:
                parts.append("Frontmatter: " + serialized)

    parts.append(memory.text)
    return "\n\n".join(part for part in parts if part)


def _serialize_mapping(mapping: Mapping[str, Any]) -> str:
    pairs: list[str] = []
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            nested = _serialize_mapping(value)
            if nested:
                pairs.append(f"{key}={{{nested}}}")
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
            pairs.append(f"{key}=[{rendered}]")
            continue
        pairs.append(f"{key}={value}")
    return "; ".join(pairs)