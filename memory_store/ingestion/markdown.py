"""Markdown parsing helpers for deterministic document ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import IngestionError
from .frontmatter import parse_frontmatter

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_PATTERN = re.compile(r"^\s*([`~]{3,})")


def _normalize_newlines(text: str) -> str:
	return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class MarkdownSection:
	heading_path: tuple[str, ...]
	text: str
	section_index: int


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
	path: Path
	raw_text: str
	body_text: str
	frontmatter: dict[str, Any]
	title: str | None
	description: str | None
	tags: tuple[str, ...]
	sections: tuple[MarkdownSection, ...]


def read_markdown_file(path: str | Path) -> MarkdownDocument:
	document_path = Path(path)
	if not document_path.exists() or not document_path.is_file():
		raise IngestionError(f"Markdown file was not found: {document_path}")

	try:
		raw_text = _normalize_newlines(document_path.read_text(encoding="utf-8"))
	except OSError as exc:
		raise IngestionError(f"Failed to read markdown file {document_path}: {exc}") from exc

	frontmatter, body_text = parse_frontmatter(raw_text)
	sections, first_heading = extract_sections(body_text)

	title = None
	if isinstance(frontmatter.get("name"), str):
		title = str(frontmatter["name"])
	elif first_heading is not None:
		title = first_heading

	description = frontmatter.get("description")
	if description is not None:
		description = str(description)

	tags = tuple(str(tag) for tag in frontmatter.get("tags", []))

	return MarkdownDocument(
		path=document_path,
		raw_text=raw_text,
		body_text=body_text,
		frontmatter=frontmatter,
		title=title,
		description=description,
		tags=tags,
		sections=tuple(sections),
	)


def extract_sections(markdown_text: str) -> tuple[list[MarkdownSection], str | None]:
	"""Split markdown into deterministic heading-scoped sections."""

	lines = _normalize_newlines(markdown_text).splitlines(keepends=True)
	sections: list[MarkdownSection] = []
	current_lines: list[str] = []
	heading_stack: list[str] = []
	active_heading_path: tuple[str, ...] = ()
	first_heading: str | None = None
	fence_marker: str | None = None

	def flush_current() -> None:
		text = "".join(current_lines).strip("\n")
		current_lines.clear()
		if not text.strip():
			return
		sections.append(
			MarkdownSection(
				heading_path=active_heading_path,
				text=text,
				section_index=len(sections),
			)
		)

	for line in lines:
		marker = _match_fence(line)

		if fence_marker is None:
			heading_match = _HEADING_PATTERN.match(line)
			if heading_match is not None:
				flush_current()
				level = len(heading_match.group(1))
				heading = heading_match.group(2).strip()
				heading_stack[:] = heading_stack[: level - 1]
				heading_stack.append(heading)
				active_heading_path = tuple(heading_stack)
				if first_heading is None:
					first_heading = heading
				continue

		current_lines.append(line)

		if marker is None:
			continue
		if fence_marker is None:
			fence_marker = marker
			continue
		if marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
			fence_marker = None

	flush_current()
	return sections, first_heading


def _match_fence(line: str) -> str | None:
	match = _FENCE_PATTERN.match(line)
	if match is None:
		return None
	return match.group(1)
