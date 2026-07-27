"""Provider contracts and registry helpers for model backends."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..errors import ProviderError

EMBEDDING_CAPABILITY = "embedding"
RERANKER_CAPABILITY = "reranker"


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    provider: str
    capability: str
    model_name: str
    revision: str | None = None
    digest: str | None = None
    vector_dimension: int | None = None
    normalization: bool | None = None
    manifest_path: Path | None = None

    @property
    def persistence_version(self) -> str | None:
        return self.revision or self.digest


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    ready: bool
    provider: str
    capability: str
    model_name: str
    safe_error_code: str | None = None


class EmbeddingProvider(Protocol):
    def available(self) -> bool: ...

    def identity(self) -> ModelIdentity: ...

    def health(self) -> ProviderHealth: ...

    def embed_text(self, text: str) -> Any: ...

    def embed_batch(self, texts: Sequence[str]) -> list[Any]: ...


class RerankerProvider(Protocol):
    def available(self) -> bool: ...

    def identity(self) -> ModelIdentity: ...

    def health(self) -> ProviderHealth: ...

    def rerank(self, query_text: str, documents: Sequence[str], *, top_n: int) -> list[float]: ...


class ProviderRegistry:
    """Maps configured provider names to embedding and reranker factories."""

    def __init__(self) -> None:
        self._embedding_factories: dict[str, Callable[[Any], EmbeddingProvider]] = {}
        self._reranker_factories: dict[str, Callable[[Any], RerankerProvider]] = {}

    def register_embedding(
        self,
        name: str,
        factory: Callable[[Any], EmbeddingProvider],
    ) -> None:
        self._embedding_factories[name.lower()] = factory

    def register_reranker(
        self,
        name: str,
        factory: Callable[[Any], RerankerProvider],
    ) -> None:
        self._reranker_factories[name.lower()] = factory

    def create_embedding(self, name: str, settings: Any) -> EmbeddingProvider:
        try:
            factory = self._embedding_factories[name.lower()]
        except KeyError as exc:
            raise ProviderError(
                f"Embedding provider is not registered: {name}",
                code="provider_not_registered",
            ) from exc
        return factory(settings)

    def create_reranker(self, name: str, settings: Any) -> RerankerProvider:
        try:
            factory = self._reranker_factories[name.lower()]
        except KeyError as exc:
            raise ProviderError(
                f"Reranker provider is not registered: {name}",
                code="provider_not_registered",
            ) from exc
        return factory(settings)