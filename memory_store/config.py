"""Typed configuration models and settings resolution helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from .errors import ConfigError
from .models import SensitivityLevel


class SettingsModel(BaseModel):
    """Shared base model for nested settings sections."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatabaseSettings(SettingsModel):
    path: Path = Path("./data/memory_store")
    create_if_missing: bool = True
    schema_version: int = Field(default=1, ge=1)
    embedded_single_process: bool = True


class EmbeddingSettings(SettingsModel):
    provider: str = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    model_version: str | None = None
    dim: int | None = Field(default=384, ge=1)
    batch_size: int = Field(default=64, ge=1)
    normalize: bool = True
    dimension_mismatch: Literal["error", "quarantine", "reembed"] = "error"


class RerankerSettings(SettingsModel):
    enabled: bool = True
    provider: str = "fastembed"
    model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_version: str | None = None
    top_n: int = Field(default=60, ge=1)


class RetrievalSettings(SettingsModel):
    vector_top_n: int = Field(default=30, ge=1)
    fts_top_n: int = Field(default=30, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    graph_expansion_enabled: bool = True
    graph_expansion_hops: int = Field(default=1, ge=0, le=1)
    final_top_k: int = Field(default=10, ge=1)
    include_component_scores: bool = True
    include_debug: bool = True


class ScoringWeights(SettingsModel):
    reranker: float = Field(default=0.45, ge=0.0, le=1.0)
    retrieval_fusion: float = Field(default=0.15, ge=0.0, le=1.0)
    vector: float = Field(default=0.10, ge=0.0, le=1.0)
    full_text: float = Field(default=0.08, ge=0.0, le=1.0)
    temporal: float = Field(default=0.07, ge=0.0, le=1.0)
    importance: float = Field(default=0.06, ge=0.0, le=1.0)
    confidence: float = Field(default=0.04, ge=0.0, le=1.0)
    graph: float = Field(default=0.03, ge=0.0, le=1.0)
    user_rating: float = Field(default=0.02, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_total_weight(self) -> Self:
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("At least one scoring weight must be greater than zero.")
        return self


class TemporalRuleSettings(SettingsModel):
    behavior: str
    half_life_days: int | None = Field(default=None, ge=1)


def _temporal_rule(behavior: str, half_life_days: int | None) -> TemporalRuleSettings:
    return TemporalRuleSettings(behavior=behavior, half_life_days=half_life_days)


class TemporalScoringSettings(SettingsModel):
    user_preference: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("slow_decay", 365)
    )
    project_fact: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("moderate_decay", 180)
    )
    task_state: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("strong_recency", 30)
    )
    conversation_summary: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("moderate_decay", 60)
    )
    observation: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("strong_decay", 14)
    )
    error_debug_note: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("strong_decay", 21)
    )
    decision: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("no_decay_unless_superseded", None)
    )
    document_chunk: TemporalRuleSettings = Field(
        default_factory=lambda: _temporal_rule("source_version_controls_freshness", None)
    )


class ScoringSettings(SettingsModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    temporal: TemporalScoringSettings = Field(default_factory=TemporalScoringSettings)


class ChunkingSettings(SettingsModel):
    strategy: str = "markdown_section"
    max_tokens: int = Field(
        default=350,
        ge=1,
        description="Approximate whitespace-token budget per chunk; not a strict tokenizer cap.",
    )
    overlap_tokens: int = Field(
        default=50,
        ge=0,
        description="Approximate whitespace-token overlap carried into the next chunk.",
    )
    include_heading_path: bool = True
    include_frontmatter_in_embedding: bool = True
    preserve_code_blocks: bool = True
    removed_chunk_policy: Literal["mark_removed", "hard_delete"] = "mark_removed"


class IngestionSettings(SettingsModel):
    max_chunks_per_document_batch: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of chunk candidates passed to one embed_batch() call. "
            "When null, reuse embeddings.batch_size."
        ),
    )
    max_chunks_per_transaction: int = Field(
        default=128,
        ge=1,
        description="Maximum number of chunk write operations in one ArcadeDB transaction.",
    )
    max_file_size_bytes: int | None = Field(default=4 * 1024 * 1024, ge=1)
    max_sections: int | None = Field(default=2_000, ge=1)
    max_chunks: int | None = Field(default=10_000, ge=1)
    max_frontmatter_bytes: int | None = Field(default=65_536, ge=1)


class PrivacySettings(SettingsModel):
    default_sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    allow_llm_context_default: bool = True
    allow_retrieval_default: bool = True
    delete_by_scope_requires_confirm: bool = True


class ApiSettings(SettingsModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    local_api_token: str | None = None


class LoggingSettings(SettingsModel):
    level: str = "INFO"


class MemoryStoreSettings(SettingsModel):
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> MemoryStoreSettings:
    """Resolve settings using defaults < YAML < env vars < explicit overrides."""

    merged: dict[str, Any] = MemoryStoreSettings().model_dump(mode="python")

    if config_path is not None:
        merged = _deep_merge(merged, _load_yaml(Path(config_path)))

    merged = _deep_merge(merged, _read_environment())
    merged = _deep_merge(merged, _normalize_mapping(overrides))

    try:
        return MemoryStoreSettings.model_validate(merged)
    except PydanticValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def _load_yaml(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")

    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError("PyYAML is required to load config files.") from exc

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config file must contain a mapping at the top level.")
    return _normalize_mapping(loaded)


def _read_environment(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    prefix = "MEMORY_STORE_"
    values: dict[str, Any] = {}

    for key, value in (environ or os.environ).items():
        if not key.startswith(prefix):
            continue

        path = [segment.lower() for segment in key.removeprefix(prefix).split("__") if segment]
        if not path:
            continue
        _assign_nested(values, path, value)

    return values


def _assign_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    current = target
    for segment in path[:-1]:
        nested = current.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            current[segment] = nested
        current = nested
    current[path[-1]] = value


def _deep_merge(base: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)

    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(dict(current), value)
            continue
        merged[key] = value

    return merged


def _normalize_mapping(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return {str(key): _normalize_mapping(item) for key, item in value.items()}
    return value
