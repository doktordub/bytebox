"""Frontmatter parsing and validation helpers for markdown ingestion."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError
from pydantic import field_validator

from ..errors import IngestionError

try:
	import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is declared in pyproject
	yaml = None


class FrontmatterModel(BaseModel):
	"""Validated subset of supported markdown frontmatter fields."""

	model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

	name: str | None = Field(default=None, max_length=1024)
	description: str | None = Field(default=None, max_length=8192)
	version: str | None = Field(default=None, max_length=32)
	owner: str | None = Field(default=None, max_length=256)
	tags: list[str] = Field(default_factory=list)

	@field_validator("tags", mode="before")
	@classmethod
	def validate_tags(cls, value: Any) -> list[str]:
		if value is None:
			return []
		if not isinstance(value, list):
			raise ValueError("tags must be an array of strings.")

		normalized: list[str] = []
		for item in value:
			if not isinstance(item, str):
				raise ValueError("tags must be an array of strings.")
			stripped = item.strip()
			if stripped:
				normalized.append(stripped)
		return normalized


def parse_frontmatter(source_text: str) -> tuple[dict[str, Any], str]:
	"""Return validated frontmatter and the markdown body without the YAML header."""

	if not source_text.startswith("---"):
		return {}, source_text

	lines = source_text.splitlines(keepends=True)
	if not lines or lines[0].strip() != "---":
		return {}, source_text

	closing_index: int | None = None
	for index, line in enumerate(lines[1:], start=1):
		if line.strip() == "---":
			closing_index = index
			break

	if closing_index is None:
		raise IngestionError("Frontmatter block is missing a closing '---' delimiter.")

	if yaml is None:
		raise IngestionError("PyYAML is required to parse markdown frontmatter.")

	raw_frontmatter = "".join(lines[1:closing_index])
	body = "".join(lines[closing_index + 1 :]).lstrip("\r\n")

	try:
		loaded = yaml.safe_load(raw_frontmatter) or {}
	except Exception as exc:  # pragma: no cover - safe_load raises many YAML-specific types
		raise IngestionError(f"Invalid YAML frontmatter: {exc}") from exc

	if not isinstance(loaded, dict):
		raise IngestionError("Frontmatter must deserialize to a mapping.")

	try:
		validated = FrontmatterModel.model_validate(loaded)
	except PydanticValidationError as exc:
		raise IngestionError(f"Invalid frontmatter fields: {exc}") from exc

	result = validated.model_dump(mode="python", exclude_none=True)
	if "tags" not in loaded and not validated.tags:
		result.pop("tags", None)

	return result, body
