"""FastEmbed provider wrappers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.util import find_spec
from math import sqrt
from typing import Any

from ..errors import PersistenceError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_text_embedding_class() -> type[Any]:
    from fastembed import TextEmbedding

    return TextEmbedding


def _read_fastembed_package_version() -> str | None:
    try:
        return package_version("fastembed")
    except PackageNotFoundError:
        return None


def _normalize_vector(values: Sequence[float]) -> list[float]:
    norm = sqrt(sum(float(value) * float(value) for value in values))
    if norm == 0.0:
        return [float(value) for value in values]
    return [float(value) / norm for value in values]


def _resolve_model_version(runtime: Any, configured_version: str | None) -> str | None:
    if configured_version is not None:
        return configured_version

    try:
        description = runtime._get_model_description(runtime.model_name)
        sources = getattr(description, "sources", None)
        source_ref = getattr(sources, "hf", None)
        if source_ref:
            return str(source_ref)
    except Exception:
        pass

    package_version_value = _read_fastembed_package_version()
    if package_version_value is not None:
        return f"fastembed:{package_version_value}"
    return None


@dataclass(frozen=True, slots=True)
class EmbeddedText:
    vector: list[float]
    model: str
    model_version: str | None
    dim: int
    created_at: datetime


@dataclass(slots=True)
class FastEmbedProvider:
    model: str = "BAAI/bge-small-en-v1.5"
    model_version: str | None = None
    batch_size: int = 64
    normalize: bool = True
    reranker_model: str | None = None
    _runtime: Any | None = field(default=None, init=False, repr=False)

    def available(self) -> bool:
        return fastembed_runtime_available()

    def embed_text(self, text: str) -> EmbeddedText:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[EmbeddedText]:
        if not texts:
            return []

        runtime = self._get_runtime()
        created_at = _utcnow()
        model_name = str(getattr(runtime, "model_name", self.model))
        model_version = _resolve_model_version(runtime, self.model_version)

        embedded: list[EmbeddedText] = []
        for raw_vector in runtime.embed(list(texts), batch_size=self.batch_size):
            vector = [float(value) for value in raw_vector]
            if self.normalize:
                vector = _normalize_vector(vector)
            embedded.append(
                EmbeddedText(
                    vector=vector,
                    model=model_name,
                    model_version=model_version,
                    dim=len(vector),
                    created_at=created_at,
                )
            )

        if len(embedded) != len(texts):
            raise PersistenceError(
                "FastEmbed returned a different number of embeddings than requested."
            )

        return embedded

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime

        if not self.available():
            raise PersistenceError("FastEmbed runtime is not available.")

        try:
            text_embedding_cls = _load_text_embedding_class()
            self._runtime = text_embedding_cls(model_name=self.model)
        except Exception as exc:
            raise PersistenceError(
                f"Failed to initialize FastEmbed model '{self.model}': {exc}"
            ) from exc

        return self._runtime


def fastembed_runtime_available() -> bool:
    return find_spec("fastembed") is not None
