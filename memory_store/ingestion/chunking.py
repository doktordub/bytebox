"""Deterministic markdown section chunking helpers.

`max_tokens` is intentionally treated as an approximate whitespace-token budget,
not as a strict tokenizer-backed cap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..errors import IngestionError
from .markdown import MarkdownSection

_FENCE_PATTERN = re.compile(r"^\s*([`~]{3,})")


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    heading_path: tuple[str, ...]
    chunk_index: int
    section_index: int
    text: str
    approximate_token_count: int


def approximate_token_count(text: str) -> int:
    """Estimate token count with whitespace splitting.

    This is used for chunk budgeting and diagnostics only. It does not match model-
    specific tokenization exactly.
    """
    return len(re.findall(r"\S+", text))


def chunk_markdown_sections(
    sections: Sequence[MarkdownSection],
    *,
    strategy: str = "markdown_section",
    max_tokens: int = 350,
    overlap_tokens: int = 50,
    include_heading_path: bool = True,
    preserve_code_blocks: bool = True,
) -> list[MarkdownChunk]:
    if strategy != "markdown_section":
        raise IngestionError(f"Unsupported chunking strategy: {strategy}")

    chunks: list[MarkdownChunk] = []
    for section in sections:
        chunk_texts = _chunk_section_text(
            section.text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            preserve_code_blocks=preserve_code_blocks,
        )
        for chunk_index, text in enumerate(chunk_texts):
            approximate_tokens = approximate_token_count(text)
            chunks.append(
                MarkdownChunk(
                    heading_path=section.heading_path if include_heading_path else (),
                    chunk_index=chunk_index,
                    section_index=section.section_index,
                    text=text,
                    approximate_token_count=approximate_tokens,
                )
            )
    return chunks


def _chunk_section_text(
    section_text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    preserve_code_blocks: bool,
) -> list[str]:
    if approximate_token_count(section_text) <= max_tokens:
        return [section_text]

    blocks = _split_blocks(section_text, preserve_code_blocks=preserve_code_blocks)
    if not blocks:
        return [section_text]

    chunks: list[str] = []
    current_blocks: list[str] = []
    current_token_count = 0

    for block in blocks:
        if preserve_code_blocks and _is_fenced_code_block(block):
            if current_blocks:
                chunks.append("\n\n".join(current_blocks).strip("\n"))
                current_blocks = []
                current_token_count = 0
            chunks.append(block)
            continue

        block_token_count = approximate_token_count(block)
        projected = current_token_count + block_token_count

        if current_blocks and projected > max_tokens:
            finalized = "\n\n".join(current_blocks).strip("\n")
            chunks.append(finalized)

            current_blocks = []
            if not (preserve_code_blocks and _is_fenced_code_block(block)):
                overlap_text = _tail_tokens(finalized, overlap_tokens)
                if overlap_text:
                    current_blocks.append(overlap_text)
            current_blocks.append(block)
            current_token_count = approximate_token_count("\n\n".join(current_blocks))
            continue

        current_blocks.append(block)
        current_token_count = approximate_token_count("\n\n".join(current_blocks))

    if current_blocks:
        chunks.append("\n\n".join(current_blocks).strip("\n"))

    return chunks


def _split_blocks(section_text: str, *, preserve_code_blocks: bool) -> list[str]:
    if not preserve_code_blocks:
        return [part.strip("\n") for part in re.split(r"\n\s*\n", section_text) if part.strip()]

    blocks: list[str] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    fence_marker: str | None = None

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = "\n".join(paragraph_lines).strip("\n")
        paragraph_lines.clear()
        if paragraph.strip():
            blocks.append(paragraph)

    def flush_code() -> None:
        if not code_lines:
            return
        block = "\n".join(code_lines).strip("\n")
        code_lines.clear()
        if block.strip():
            blocks.append(block)

    for line in section_text.splitlines():
        marker = _match_fence(line)
        if fence_marker is None and marker is not None:
            flush_paragraph()
            fence_marker = marker
            code_lines.append(line)
            continue

        if fence_marker is not None:
            code_lines.append(line)
            if (
                marker is not None
                and marker[0] == fence_marker[0]
                and len(marker) >= len(fence_marker)
            ):
                fence_marker = None
                flush_code()
            continue

        if not line.strip():
            flush_paragraph()
            continue
        paragraph_lines.append(line)

    flush_paragraph()
    flush_code()
    return blocks


def _tail_tokens(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""

    tokens = text.split()
    if not tokens:
        return ""
    return " ".join(tokens[-overlap_tokens:])


def _match_fence(line: str) -> str | None:
    match = _FENCE_PATTERN.match(line)
    if match is None:
        return None
    return match.group(1)


def _is_fenced_code_block(block: str) -> bool:
    lines = block.splitlines()
    if len(lines) < 2:
        return False
    opening = _match_fence(lines[0])
    closing = _match_fence(lines[-1])
    if opening is None or closing is None:
        return False
    return opening[0] == closing[0] and len(closing) >= len(opening)
