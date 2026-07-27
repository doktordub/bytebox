from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import memory_store.embeddings.fastembed_provider as fastembed_provider_module
from memory_store.embeddings.fastembed_provider import FastEmbedProvider, FastEmbedRerankerProvider
from memory_store.embeddings.manifests import build_model_manifest_from_directory, write_model_manifest
from memory_store.errors import ProviderError


class _FakeTextEmbedding:
    init_calls: list[dict[str, object]] = []

    def __init__(self, model_name: str, **_: object) -> None:
        type(self).init_calls.append({"model_name": model_name, **_})
        self.model_name = model_name

    def embed(self, texts: list[str], batch_size: int = 64):
        assert batch_size == 2
        vectors = {
            "first": [3.0, 4.0, 0.0, 0.0],
            "second": [0.0, 5.0, 0.0, 0.0],
        }
        for text in texts:
            yield vectors[text]


class _FakeTextCrossEncoder:
    init_count = 0

    def __init__(self, model_name: str, **_: object) -> None:
        type(self).init_count += 1
        self.model_name = model_name

    def rerank(self, query_text: str, documents: list[str], batch_size: int = 32):
        assert query_text == "thin adapters"
        assert batch_size == 2
        return [0.8, 0.2][: len(documents)]


def _write_manifest(model_path: Path) -> Path:
    manifest = build_model_manifest_from_directory(
        model_path,
        provider="fastembed",
        capability="embedding",
        model_name="stub-model",
        revision="stub/revision",
        digest="sha256:manifest",
        vector_dimension=4,
        normalization=True,
    )
    manifest_path = model_path / "bytebox-model.yaml"
    write_model_manifest(manifest_path, manifest)
    return manifest_path


def test_fastembed_provider_batch_normalizes_and_preserves_order(monkeypatch) -> None:
    _FakeTextEmbedding.init_calls.clear()
    monkeypatch.setattr(
        fastembed_provider_module,
        "_load_text_embedding_class",
        lambda: _FakeTextEmbedding,
    )

    provider = FastEmbedProvider(
        model="stub-model",
        model_revision="stub/revision",
        batch_size=2,
        expected_dim=4,
        normalize=True,
    )
    embedded = provider.embed_batch(["first", "second"])

    assert len(embedded) == 2
    assert [item.model for item in embedded] == ["stub-model", "stub-model"]
    assert [item.model_version for item in embedded] == ["stub/revision", "stub/revision"]
    assert [item.dim for item in embedded] == [4, 4]
    assert embedded[0].vector == [0.6, 0.8, 0.0, 0.0]
    assert embedded[1].vector == [0.0, 1.0, 0.0, 0.0]
    assert embedded[0].created_at == embedded[1].created_at


def test_fastembed_provider_uses_manifest_identity_and_runtime_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "model.onnx").write_bytes(b"fake-model")
    manifest_path = _write_manifest(model_path)
    cache_dir = tmp_path / "cache"

    _FakeTextEmbedding.init_calls.clear()
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(
        fastembed_provider_module,
        "_load_text_embedding_class",
        lambda: _FakeTextEmbedding,
    )

    provider = FastEmbedProvider(
        model="stub-model",
        model_path=model_path,
        cache_dir=cache_dir,
        local_files_only=True,
        hf_hub_offline=True,
        threads=4,
        execution_providers=("CPUExecutionProvider",),
        expected_dim=4,
        normalize=True,
        manifest_path=manifest_path,
        require_manifest=True,
        require_checksums=True,
        batch_size=2,
    )

    embedded = provider.embed_batch(["first", "second"])

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert _FakeTextEmbedding.init_calls[0]["model_name"] == str(model_path)
    assert _FakeTextEmbedding.init_calls[0]["cache_dir"] == cache_dir
    assert _FakeTextEmbedding.init_calls[0]["local_files_only"] is True
    assert _FakeTextEmbedding.init_calls[0]["threads"] == 4
    assert _FakeTextEmbedding.init_calls[0]["providers"] == ["CPUExecutionProvider"]
    assert [item.model_version for item in embedded] == ["stub/revision", "stub/revision"]
    assert provider.identity().digest == "sha256:manifest"


def test_fastembed_provider_hides_runtime_error_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fastembed_provider_module,
        "_load_text_embedding_class",
        lambda: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    provider = FastEmbedProvider(model="stub-model")

    with pytest.raises(ProviderError) as exc_info:
        provider.embed_text("thin adapters")

    assert exc_info.value.code == "provider_init_failed"
    assert "secret" not in str(exc_info.value)


def test_fastembed_reranker_provider_reuses_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeTextCrossEncoder.init_count = 0
    monkeypatch.setattr(
        fastembed_provider_module,
        "_load_text_cross_encoder_class",
        lambda: _FakeTextCrossEncoder,
    )

    provider = FastEmbedRerankerProvider(model="reranker-model", batch_size=2)

    first = provider.rerank("thin adapters", ["doc one", "doc two"], top_n=2)
    second = provider.rerank("thin adapters", ["doc one", "doc two"], top_n=2)

    assert first == [0.8, 0.2]
    assert second == [0.8, 0.2]
    assert _FakeTextCrossEncoder.init_count == 1