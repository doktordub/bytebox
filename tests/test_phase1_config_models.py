from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from bytebox.config import ByteBoxSettings, load_settings
from bytebox.errors import ConfigError
from bytebox.models import (
    MemoryCreate,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
    Scope,
    SensitivityLevel,
)


def test_load_settings_defaults() -> None:
    settings = load_settings()

    assert isinstance(settings, ByteBoxSettings)
    assert settings.database.path == Path("./data/bytebox")
    assert settings.embeddings.local_files_only is False
    assert settings.embeddings.execution_providers == []
    assert settings.reranker.enabled is True
    assert settings.retrieval.final_top_k == 10
    assert settings.scoring.weights.reranker == pytest.approx(0.45)


def test_load_settings_yaml_override(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
database:
  path: ./custom/db
retrieval:
  final_top_k: 25
privacy:
  default_sensitivity: private
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.database.path == Path("./custom/db")
    assert settings.retrieval.final_top_k == 25
    assert settings.privacy.default_sensitivity == SensitivityLevel.PRIVATE


def test_load_settings_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BYTEBOX_DATABASE__PATH", str(tmp_path / "env-db"))
    monkeypatch.setenv("BYTEBOX_RETRIEVAL__FINAL_TOP_K", "17")
    monkeypatch.setenv("BYTEBOX_SCORING__WEIGHTS__RERANKER", "0.3")
    monkeypatch.setenv(
        "BYTEBOX_EMBEDDINGS__EXECUTION_PROVIDERS",
        "CPUExecutionProvider, CUDAExecutionProvider",
    )
    monkeypatch.setenv("BYTEBOX_EMBEDDINGS__HF_HUB_OFFLINE", "true")

    settings = load_settings()

    assert settings.database.path == tmp_path / "env-db"
    assert settings.retrieval.final_top_k == 17
    assert settings.scoring.weights.reranker == pytest.approx(0.3)
    assert settings.embeddings.execution_providers == [
        "CPUExecutionProvider",
        "CUDAExecutionProvider",
    ]
    assert settings.embeddings.hf_hub_offline is True


def test_load_settings_supports_remote_provider_transport_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYTEBOX_EMBEDDINGS__REMOTE__BASE_URL", "https://models.internal:11434")
    monkeypatch.setenv(
        "BYTEBOX_EMBEDDINGS__REMOTE__ALLOWED_HOSTS",
        "models.internal, backup.internal",
    )
    monkeypatch.setenv(
        "BYTEBOX_RERANKER__REMOTE__ALLOWED_CIDRS",
        "10.0.0.0/8,192.168.0.0/16",
    )
    monkeypatch.setenv("BYTEBOX_RERANKER__REMOTE__VERIFY_TLS", "false")

    settings = load_settings()

    assert settings.embeddings.remote.base_url == "https://models.internal:11434"
    assert settings.embeddings.remote.allowed_hosts == [
        "models.internal",
        "backup.internal",
    ]
    assert settings.reranker.remote.allowed_cidrs == ["10.0.0.0/8", "192.168.0.0/16"]
    assert settings.reranker.remote.verify_tls is False


def test_load_settings_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
api:
  port: 9000
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("BYTEBOX_API__PORT", "9100")

    settings = load_settings(config_path, api={"port": 9200})

    assert settings.api.port == 9200


def test_load_settings_production_disables_verbose_retrieval_diagnostics_by_default() -> None:
    settings = load_settings(application={"environment": "production"})

    assert settings.retrieval.include_component_scores is False
    assert settings.retrieval.include_debug is False


def test_load_settings_production_allows_explicit_retrieval_diagnostics_override() -> None:
    settings = load_settings(
        application={"environment": "production"},
        retrieval={
            "include_component_scores": True,
            "include_debug": True,
        },
    )

    assert settings.retrieval.include_component_scores is True
    assert settings.retrieval.include_debug is True


def test_load_settings_promotes_legacy_local_api_tokens_into_scoped_auth() -> None:
    settings = load_settings(
        api={
            "local_api_token": "secret-token",
            "rotated_api_tokens": ["older-secret"],
            "local_api_token_scopes": "memory:read,admin:read",
            "tls": {
                "enabled": True,
                "cert_file": "tls/server.crt",
                "key_file": "tls/server.key",
                "client_ca_file": "tls/ca.crt",
                "require_client_certificate": True,
            },
        },
        security={"ingest_roots": ["./docs", "./examples"]},
    )

    assert settings.api.auth.enabled is True
    assert [token.name for token in settings.api.auth.tokens] == [
        "local-primary",
        "local-rotated-1",
    ]
    assert [token.token.get_secret_value() for token in settings.api.auth.tokens] == [
        "secret-token",
        "older-secret",
    ]
    assert settings.api.auth.tokens[0].scopes == ["memory:read", "admin:read"]
    assert settings.api.tls.require_client_certificate is True
    assert settings.security.ingest_roots == [Path("./docs"), Path("./examples")]


def test_load_settings_rejects_incomplete_mtls_configuration() -> None:
    with pytest.raises(ConfigError):
        load_settings(
            api={
                "tls": {
                    "enabled": True,
                    "cert_file": "tls/server.crt",
                    "key_file": "tls/server.key",
                    "require_client_certificate": True,
                }
            }
        )


def test_memory_create_requires_text() -> None:
    with pytest.raises(PydanticValidationError):
        MemoryCreate(scope=Scope(project_id="bb1"), memory_type=MemoryType.DECISION)


def test_memory_create_rejects_invalid_enum_value() -> None:
    with pytest.raises(PydanticValidationError):
        MemoryCreate(scope=Scope(project_id="bb1"), text="x", memory_type="wrong")


def test_memory_create_rejects_out_of_range_scores() -> None:
    with pytest.raises(PydanticValidationError):
        MemoryCreate(scope=Scope(project_id="bb1"), text="x", confidence=1.5)


def test_memory_search_result_supports_raw_and_normalized_scores() -> None:
    record = MemoryRecord(
        memory_id="mem-1",
        scope=Scope(user_id="user-1", project_id="bb1"),
        memory_type=MemoryType.DECISION,
        title="Use FastEmbed reranker",
        text="Use FastEmbed reranker after hybrid retrieval.",
        confidence=0.95,
        importance=0.8,
    )

    result = MemorySearchResult(
        record=record,
        final_score=0.92,
        component_scores={"vector": 12.4, "reranker": 5.8},
        normalized_scores={"vector": 0.81, "reranker": 0.96},
        debug={"fused_rank": 1},
    )

    assert result.memory.title == "Use FastEmbed reranker"
    assert result.record.memory_id == "mem-1"
    assert result.component_scores["vector"] == pytest.approx(12.4)
    assert result.normalized_scores["reranker"] == pytest.approx(0.96)


def test_memory_search_result_rejects_invalid_normalized_score() -> None:
    record = MemoryRecord(
        memory_id="mem-2",
        scope=Scope(project_id="bb1"),
        memory_type=MemoryType.PROJECT_FACT,
        text="ArcadeDB Embedded is the only persistence engine.",
    )

    with pytest.raises(PydanticValidationError):
        MemorySearchResult(
            memory=record,
            final_score=0.6,
            normalized_scores={"vector": 1.2},
        )
