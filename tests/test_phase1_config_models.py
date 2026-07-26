from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from memory_store.config import MemoryStoreSettings, load_settings
from memory_store.models import (
    MemoryCreate,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
    Scope,
    SensitivityLevel,
)


def test_load_settings_defaults() -> None:
    settings = load_settings()

    assert isinstance(settings, MemoryStoreSettings)
    assert settings.database.path == Path("./data/memory_store")
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


def test_load_settings_environment_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEMORY_STORE_DATABASE__PATH", str(tmp_path / "env-db"))
    monkeypatch.setenv("MEMORY_STORE_RETRIEVAL__FINAL_TOP_K", "17")
    monkeypatch.setenv("MEMORY_STORE_SCORING__WEIGHTS__RERANKER", "0.3")

    settings = load_settings()

    assert settings.database.path == tmp_path / "env-db"
    assert settings.retrieval.final_top_k == 17
    assert settings.scoring.weights.reranker == pytest.approx(0.3)


def test_load_settings_explicit_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
api:
  port: 9000
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORY_STORE_API__PORT", "9100")

    settings = load_settings(config_path, api={"port": 9200})

    assert settings.api.port == 9200


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