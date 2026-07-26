from __future__ import annotations

from types import SimpleNamespace

import memory_store.embeddings.fastembed_provider as fastembed_provider_module
from memory_store.embeddings.fastembed_provider import FastEmbedProvider


class _FakeTextEmbedding:
    def __init__(self, model_name: str, **_: object) -> None:
        self.model_name = model_name

    def embed(self, texts: list[str], batch_size: int = 64):
        assert batch_size == 2
        vectors = {
            "first": [3.0, 4.0, 0.0, 0.0],
            "second": [0.0, 5.0, 0.0, 0.0],
        }
        for text in texts:
            yield vectors[text]

    def _get_model_description(self, model_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            model=model_name,
            dim=4,
            sources=SimpleNamespace(hf="stub/revision"),
        )


def test_fastembed_provider_batch_normalizes_and_preserves_order(monkeypatch) -> None:
    monkeypatch.setattr(
        fastembed_provider_module,
        "_load_text_embedding_class",
        lambda: _FakeTextEmbedding,
    )

    provider = FastEmbedProvider(model="stub-model", batch_size=2, normalize=True)
    embedded = provider.embed_batch(["first", "second"])

    assert len(embedded) == 2
    assert [item.model for item in embedded] == ["stub-model", "stub-model"]
    assert [item.model_version for item in embedded] == ["stub/revision", "stub/revision"]
    assert [item.dim for item in embedded] == [4, 4]
    assert embedded[0].vector == [0.6, 0.8, 0.0, 0.0]
    assert embedded[1].vector == [0.0, 1.0, 0.0, 0.0]
    assert embedded[0].created_at == embedded[1].created_at