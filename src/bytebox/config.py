"""Typed configuration models and settings resolution helpers."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from .auth import ALL_AUTH_SCOPES, normalize_auth_scopes
from .errors import ConfigError
from .models import SensitivityLevel


class SettingsModel(BaseModel):
    """Shared base model for nested settings sections."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatabaseSettings(SettingsModel):
    path: Path = Path("./data/bytebox")
    create_if_missing: bool = True
    schema_version: int = Field(default=1, ge=1)
    embedded_single_process: bool = True


class ApplicationSettings(SettingsModel):
    environment: Literal["development", "test", "production"] = "development"
    state_dir: Path = Path("./state")
    build_commit: str | None = None
    build_time: datetime | None = None
    minimum_free_space_bytes: int = Field(default=128 * 1024 * 1024, ge=0)


def _parse_list_setting(value: Any) -> Any:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class RemoteProviderSettings(SettingsModel):
    base_url: str | None = None
    timeout_connect_seconds: float = Field(default=5.0, gt=0.0)
    timeout_read_seconds: float = Field(default=30.0, gt=0.0)
    timeout_write_seconds: float = Field(default=30.0, gt=0.0)
    timeout_pool_seconds: float = Field(default=5.0, gt=0.0)
    max_connections: int = Field(default=20, ge=1)
    max_keepalive_connections: int = Field(default=10, ge=0)
    trust_env: bool = False
    follow_redirects: bool = False
    max_redirects: int = Field(default=1, ge=0, le=5)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0)
    circuit_breaker_failures: int = Field(default=3, ge=1)
    circuit_breaker_reset_seconds: float = Field(default=5.0, gt=0.0)
    max_concurrency: int = Field(default=4, ge=1)
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_cidrs: list[str] = Field(default_factory=list)
    allow_loopback: bool = True
    allow_private_network: bool = True
    allow_link_local: bool = False
    allow_metadata_address: bool = False
    verify_tls: bool = True
    ca_bundle_path: Path | None = None
    client_cert_path: Path | None = None
    client_key_path: Path | None = None
    client_key_password: SecretStr | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("allowed_hosts", "allowed_cidrs", mode="before")
    @classmethod
    def parse_lists(cls, value: Any) -> Any:
        return _parse_list_setting(value)

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_allowed_hosts(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @field_validator("allowed_cidrs")
    @classmethod
    def validate_allowed_cidrs(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value

    @model_validator(mode="after")
    def validate_transport_settings(self) -> Self:
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("max_keepalive_connections cannot exceed max_connections.")
        if self.client_key_path is not None and self.client_cert_path is None:
            raise ValueError("client_cert_path is required when client_key_path is configured.")
        return self


class EmbeddingSettings(SettingsModel):
    provider: str = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    model_version: str | None = None
    model_path: Path | None = None
    cache_dir: Path | None = None
    local_files_only: bool = False
    hf_hub_offline: bool = False
    threads: int | None = Field(default=None, ge=1)
    execution_providers: list[str] = Field(default_factory=list)
    dim: int | None = Field(default=384, ge=1)
    batch_size: int = Field(default=64, ge=1)
    normalize: bool = True
    model_revision: str | None = None
    model_digest: str | None = None
    manifest_path: Path | None = None
    require_manifest: bool = False
    require_checksums: bool = False
    dimension_mismatch: Literal["error", "quarantine", "reembed"] = "error"
    remote: RemoteProviderSettings = Field(default_factory=RemoteProviderSettings)

    @field_validator("execution_providers", mode="before")
    @classmethod
    def parse_execution_providers(cls, value: Any) -> Any:
        return _parse_list_setting(value)


class RerankerSettings(SettingsModel):
    enabled: bool = True
    provider: str = "fastembed"
    model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_version: str | None = None
    model_path: Path | None = None
    cache_dir: Path | None = None
    local_files_only: bool = False
    hf_hub_offline: bool = False
    threads: int | None = Field(default=None, ge=1)
    execution_providers: list[str] = Field(default_factory=list)
    batch_size: int = Field(default=32, ge=1)
    model_revision: str | None = None
    model_digest: str | None = None
    manifest_path: Path | None = None
    require_manifest: bool = False
    require_checksums: bool = False
    top_n: int = Field(default=60, ge=1)
    llm_max_documents: int = Field(default=20, ge=1)
    llm_max_document_chars: int = Field(default=2_000, ge=1)
    llm_max_output_tokens: int = Field(default=512, ge=1)
    remote: RemoteProviderSettings = Field(default_factory=RemoteProviderSettings)

    @field_validator("execution_providers", mode="before")
    @classmethod
    def parse_execution_providers(cls, value: Any) -> Any:
        return _parse_list_setting(value)


class RetrievalSettings(SettingsModel):
    vector_top_n: int = Field(default=30, ge=1)
    fts_top_n: int = Field(default=30, ge=1)
    vector_candidate_multiplier: int = Field(default=4, ge=1)
    fts_candidate_multiplier: int = Field(default=4, ge=1)
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


class SecuritySettings(SettingsModel):
    ingest_roots: list[Path] = Field(default_factory=list)
    allow_symlinks: bool = False
    export_enabled: bool = True
    import_enabled: bool = True
    hard_delete_enabled: bool = False
    require_import_idempotency_key: bool = True

    @field_validator("ingest_roots", mode="before")
    @classmethod
    def parse_ingest_roots(cls, value: Any) -> Any:
        return _parse_list_setting(value)


class ApiTokenSettings(SettingsModel):
    name: str | None = None
    token: SecretStr
    scopes: list[str] = Field(default_factory=lambda: list(ALL_AUTH_SCOPES))

    @field_validator("scopes", mode="before")
    @classmethod
    def parse_scopes(cls, value: Any) -> Any:
        return _parse_list_setting(value)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        return normalize_auth_scopes(value) or list(ALL_AUTH_SCOPES)


class ApiAuthSettings(SettingsModel):
    enabled: bool = True
    tokens: list[ApiTokenSettings] = Field(default_factory=list)


class ApiTlsSettings(SettingsModel):
    enabled: bool = False
    cert_file: Path | None = None
    key_file: Path | None = None
    key_password: SecretStr | None = None
    client_ca_file: Path | None = None
    require_client_certificate: bool = False

    @model_validator(mode="after")
    def validate_tls_settings(self) -> Self:
        if not self.enabled:
            return self
        if self.cert_file is None or self.key_file is None:
            raise ValueError("API TLS requires both cert_file and key_file when enabled.")
        if self.require_client_certificate and self.client_ca_file is None:
            raise ValueError(
                "client_ca_file is required when require_client_certificate is enabled."
            )
        return self


class ApiSettings(SettingsModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    workers: int = Field(default=1, ge=1)
    docs_enabled: bool = False
    health_live_anonymous: bool = True
    health_ready_anonymous: bool = False
    metrics_enabled: bool = True
    metrics_anonymous: bool = False
    max_request_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )
    auth: ApiAuthSettings = Field(default_factory=ApiAuthSettings)
    tls: ApiTlsSettings = Field(default_factory=ApiTlsSettings)
    local_api_token: SecretStr | None = None
    rotated_api_tokens: list[SecretStr] = Field(default_factory=list)
    local_api_token_scopes: list[str] = Field(default_factory=lambda: list(ALL_AUTH_SCOPES))

    @field_validator("trusted_hosts", "local_api_token_scopes", "rotated_api_tokens", mode="before")
    @classmethod
    def parse_api_lists(cls, value: Any) -> Any:
        return _parse_list_setting(value)

    @field_validator("trusted_hosts")
    @classmethod
    def normalize_trusted_hosts(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @field_validator("local_api_token_scopes")
    @classmethod
    def validate_local_api_token_scopes(cls, value: list[str]) -> list[str]:
        return normalize_auth_scopes(value) or list(ALL_AUTH_SCOPES)

    @model_validator(mode="after")
    def synchronize_legacy_local_tokens(self) -> Self:
        auth_tokens = list(self.auth.tokens)
        if self.local_api_token is not None:
            auth_tokens.insert(
                0,
                ApiTokenSettings(
                    name="local-primary",
                    token=self.local_api_token,
                    scopes=self.local_api_token_scopes,
                ),
            )
        for index, token in enumerate(self.rotated_api_tokens, start=1):
            auth_tokens.append(
                ApiTokenSettings(
                    name=f"local-rotated-{index}",
                    token=token,
                    scopes=self.local_api_token_scopes,
                )
            )

        self.auth.tokens = auth_tokens
        self.auth.enabled = bool(self.auth.tokens)
        return self


class LoggingSettings(SettingsModel):
    level: Literal["debug", "info", "warn", "off"] = "info"
    format: Literal["json", "console"] = "json"
    max_field_length: int = Field(default=256, ge=64)
    capture_warnings: bool = True
    metrics_enabled: bool = True
    opentelemetry_enabled: bool = False

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized == "warning":
            return "warn"
        return normalized

    @field_validator("format", mode="before")
    @classmethod
    def normalize_format(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip().lower()


class MemoryStoreSettings(SettingsModel):
    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


ByteBoxSettings = MemoryStoreSettings


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> MemoryStoreSettings:
    """Resolve settings using defaults < YAML < env vars < explicit overrides."""

    yaml_values: dict[str, Any] = {}
    env_values = _read_environment()
    override_values = _normalize_mapping(overrides)
    merged: dict[str, Any] = MemoryStoreSettings().model_dump(mode="python")

    if config_path is not None:
        yaml_values = _load_yaml(Path(config_path))
        merged = _deep_merge(merged, yaml_values)

    merged = _deep_merge(merged, env_values)
    merged = _deep_merge(merged, override_values)
    merged = _apply_environment_defaults(merged, yaml_values, env_values, override_values)

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
    prefix = "BYTEBOX_"
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


def _apply_environment_defaults(
    merged: dict[str, Any],
    *sources: Mapping[str, Any],
) -> dict[str, Any]:
    application = merged.get("application")
    if not isinstance(application, Mapping):
        return merged
    environment = str(application.get("environment", "development")).strip().lower()
    if environment != "production":
        return merged

    retrieval = dict(merged.get("retrieval", {}))
    if not any(
        _mapping_has_path(source, "retrieval", "include_component_scores") for source in sources
    ):
        retrieval["include_component_scores"] = False
    if not any(_mapping_has_path(source, "retrieval", "include_debug") for source in sources):
        retrieval["include_debug"] = False
    merged["retrieval"] = retrieval
    return merged


def _mapping_has_path(mapping: Mapping[str, Any], *path: str) -> bool:
    current: Any = mapping
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current[segment]
    return True


def _normalize_mapping(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return {str(key): _normalize_mapping(item) for key, item in value.items()}
    return value
