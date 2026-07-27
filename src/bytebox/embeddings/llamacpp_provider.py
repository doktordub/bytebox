"""llama.cpp HTTP adapters for remote embeddings and native reranking."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from typing import Any

from ..config import RemoteProviderSettings
from ..errors import ProviderError
from .fastembed_provider import EmbeddedText
from .framework import (
    EMBEDDING_CAPABILITY,
    RERANKER_CAPABILITY,
    ModelIdentity,
    ProviderHealth,
)
from .remote_http import SharedAsyncHttpClient


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_vector(values: Sequence[float]) -> list[float]:
    norm = sqrt(sum(float(value) * float(value) for value in values))
    if norm == 0.0:
        return [float(value) for value in values]
    return [float(value) / norm for value in values]


def _raise_malformed_response(message: str) -> None:
    raise ProviderError(message, code="provider_malformed_response")


@dataclass(slots=True)
class LlamaCppEmbeddingProvider:
    model: str
    remote: RemoteProviderSettings
    model_version: str | None = None
    model_revision: str | None = None
    model_digest: str | None = None
    expected_dim: int | None = None
    normalize: bool = True
    shared_client: SharedAsyncHttpClient | None = None
    _owned_client: SharedAsyncHttpClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        shared_client: SharedAsyncHttpClient | None = None,
    ) -> "LlamaCppEmbeddingProvider":
        return cls(
            model=settings.model,
            remote=getattr(settings, "remote", RemoteProviderSettings()),
            model_version=getattr(settings, "model_version", None),
            model_revision=getattr(settings, "model_revision", None),
            model_digest=getattr(settings, "model_digest", None),
            expected_dim=getattr(settings, "dim", None),
            normalize=getattr(settings, "normalize", True),
            shared_client=shared_client,
        )

    def available(self) -> bool:
        return self.remote.base_url is not None

    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            provider="llamacpp",
            capability=EMBEDDING_CAPABILITY,
            model_name=self.model,
            revision=self.model_revision or self.model_version,
            digest=self.model_digest,
            vector_dimension=self.expected_dim,
            normalization=self.normalize,
        )

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                ready=False,
                provider="llamacpp",
                capability=EMBEDDING_CAPABILITY,
                model_name=self.model,
                safe_error_code="provider_endpoint_unconfigured",
            )
        try:
            self.embed_text("ByteBox health check")
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="llamacpp",
                capability=EMBEDDING_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )
        return ProviderHealth(
            ready=True,
            provider="llamacpp",
            capability=EMBEDDING_CAPABILITY,
            model_name=self.model,
        )

    def validate_startup(self) -> None:
        self.embed_text("ByteBox startup validation")

    def embed_text(self, text: str) -> EmbeddedText:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[EmbeddedText]:
        if not texts:
            return []

        payload = self._request_with_fallback(
            paths=("/v1/embeddings", "/embeddings"),
            json_body={"input": list(texts), "model": self.model},
        )
        if not isinstance(payload, dict):
            _raise_malformed_response("llama.cpp returned malformed embedding data.")
        rows = payload.get("data")
        if not isinstance(rows, list):
            _raise_malformed_response("llama.cpp returned malformed embedding data.")

        ordered: dict[int, list[float]] = {}
        for item in rows:
            if not isinstance(item, dict):
                _raise_malformed_response("llama.cpp returned malformed embedding data.")
            index = item.get("index")
            embedding = item.get("embedding")
            if not isinstance(index, int) or not isinstance(embedding, list):
                _raise_malformed_response("llama.cpp returned malformed embedding data.")
            ordered[index] = [float(value) for value in embedding]

        if len(ordered) != len(texts):
            _raise_malformed_response("llama.cpp returned an incomplete embedding response.")

        created_at = _utcnow()
        embedded: list[EmbeddedText] = []
        for index in range(len(texts)):
            vector = ordered.get(index)
            if vector is None:
                _raise_malformed_response("llama.cpp returned an incomplete embedding response.")
            if self.expected_dim is not None and len(vector) != self.expected_dim:
                raise ProviderError(
                    "Remote provider returned an unexpected embedding dimension.",
                    code="provider_dimension_mismatch",
                )
            if self.normalize:
                vector = _normalize_vector(vector)
            embedded.append(
                EmbeddedText(
                    vector=vector,
                    model=self.model,
                    model_version=self.model_revision or self.model_version,
                    dim=len(vector),
                    created_at=created_at,
                )
            )
        return embedded

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def _client(self) -> SharedAsyncHttpClient:
        if self.shared_client is not None:
            return self.shared_client
        if self._owned_client is None:
            self._owned_client = SharedAsyncHttpClient(
                name=f"llamacpp-embedding:{self.model}",
                settings=self.remote,
            )
        return self._owned_client

    def _request_with_fallback(self, *, paths: Sequence[str], json_body: dict[str, Any]) -> Any:
        for path in paths:
            response = self._client().request_json(
                "POST",
                path,
                json_body=json_body,
                expected_statuses=(200, 404),
            )
            if response.status_code == 404:
                continue
            return response.payload
        raise ProviderError(
            "Configured llama.cpp embedding endpoint is unavailable.",
            code="provider_capability_unavailable",
        )


@dataclass(slots=True)
class LlamaCppRerankerProvider:
    model: str
    remote: RemoteProviderSettings
    model_version: str | None = None
    model_revision: str | None = None
    model_digest: str | None = None
    shared_client: SharedAsyncHttpClient | None = None
    _owned_client: SharedAsyncHttpClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        shared_client: SharedAsyncHttpClient | None = None,
    ) -> "LlamaCppRerankerProvider":
        return cls(
            model=settings.model,
            remote=getattr(settings, "remote", RemoteProviderSettings()),
            model_version=getattr(settings, "model_version", None),
            model_revision=getattr(settings, "model_revision", None),
            model_digest=getattr(settings, "model_digest", None),
            shared_client=shared_client,
        )

    def available(self) -> bool:
        return self.remote.base_url is not None

    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            provider="llamacpp",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
            revision=self.model_revision or self.model_version,
            digest=self.model_digest,
        )

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                ready=False,
                provider="llamacpp",
                capability=RERANKER_CAPABILITY,
                model_name=self.model,
                safe_error_code="provider_endpoint_unconfigured",
            )
        try:
            self.rerank("ByteBox health check", ["document"], top_n=1)
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="llamacpp",
                capability=RERANKER_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )
        return ProviderHealth(
            ready=True,
            provider="llamacpp",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
        )

    def validate_startup(self) -> None:
        self.rerank("ByteBox startup validation", ["document"], top_n=1)

    def rerank(self, query_text: str, documents: Sequence[str], *, top_n: int) -> list[float]:
        if not documents or top_n < 1:
            return []

        limited_documents = list(documents[:top_n])
        payload = self._request_with_fallback(
            paths=("/rerank", "/v1/rerank"),
            json_body={
                "model": self.model,
                "query": query_text,
                "documents": limited_documents,
                "top_n": len(limited_documents),
            },
        )
        if not isinstance(payload, dict):
            _raise_malformed_response("llama.cpp returned malformed reranker data.")
        results = payload.get("results") or payload.get("data")
        if not isinstance(results, list):
            _raise_malformed_response("llama.cpp returned malformed reranker data.")

        scores = [0.0 for _ in limited_documents]
        for item in results:
            if not isinstance(item, dict):
                _raise_malformed_response("llama.cpp returned malformed reranker data.")
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(index, int) or not isinstance(score, (int, float)):
                _raise_malformed_response("llama.cpp returned malformed reranker data.")
            if 0 <= index < len(scores):
                scores[index] = float(score)
        return scores

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def _client(self) -> SharedAsyncHttpClient:
        if self.shared_client is not None:
            return self.shared_client
        if self._owned_client is None:
            self._owned_client = SharedAsyncHttpClient(
                name=f"llamacpp-reranker:{self.model}",
                settings=self.remote,
            )
        return self._owned_client

    def _request_with_fallback(self, *, paths: Sequence[str], json_body: dict[str, Any]) -> Any:
        for path in paths:
            response = self._client().request_json(
                "POST",
                path,
                json_body=json_body,
                expected_statuses=(200, 404),
            )
            if response.status_code == 404:
                continue
            return response.payload
        raise ProviderError(
            "Configured llama.cpp reranker endpoint is unavailable.",
            code="provider_capability_unavailable",
        )
