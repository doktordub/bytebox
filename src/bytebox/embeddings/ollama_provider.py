"""Ollama HTTP adapters for remote embeddings and optional LLM reranking."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
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
class OllamaEmbeddingProvider:
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
    ) -> "OllamaEmbeddingProvider":
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
            provider="ollama",
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
                provider="ollama",
                capability=EMBEDDING_CAPABILITY,
                model_name=self.model,
                safe_error_code="provider_endpoint_unconfigured",
            )

        try:
            response = self._client().request_json("GET", "/api/tags")
            payload = response.payload
            if not isinstance(payload, dict):
                _raise_malformed_response("Ollama returned malformed model discovery data.")
            models = payload.get("models")
            if not isinstance(models, list):
                _raise_malformed_response("Ollama returned malformed model discovery data.")
            available_models = {
                str(item.get("name") or item.get("model") or "")
                for item in models
                if isinstance(item, dict)
            }
            if self.model not in available_models:
                raise ProviderError(
                    "Configured Ollama model is unavailable.",
                    code="provider_model_not_available",
                )
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="ollama",
                capability=EMBEDDING_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )

        return ProviderHealth(
            ready=True,
            provider="ollama",
            capability=EMBEDDING_CAPABILITY,
            model_name=self.model,
        )

    def validate_startup(self) -> None:
        health = self.health()
        if not health.ready:
            raise ProviderError(
                "Configured Ollama model is unavailable.",
                code=health.safe_error_code or "provider_unavailable",
            )
        self.embed_text("ByteBox startup validation")

    def embed_text(self, text: str) -> EmbeddedText:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[EmbeddedText]:
        if not texts:
            return []

        response = self._client().request_json(
            "POST",
            "/api/embed",
            json_body={"model": self.model, "input": list(texts)},
        )
        payload = response.payload
        if not isinstance(payload, dict):
            _raise_malformed_response("Ollama returned malformed embedding data.")

        raw_embeddings = payload.get("embeddings")
        if raw_embeddings is None and len(texts) == 1 and isinstance(payload.get("embedding"), list):
            raw_embeddings = [payload["embedding"]]
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            _raise_malformed_response("Ollama returned an incomplete embedding response.")

        created_at = _utcnow()
        embedded: list[EmbeddedText] = []
        for raw_vector in raw_embeddings:
            if not isinstance(raw_vector, list) or not raw_vector:
                _raise_malformed_response("Ollama returned malformed embedding data.")
            vector = [float(value) for value in raw_vector]
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
                name=f"ollama-embedding:{self.model}",
                settings=self.remote,
            )
        return self._owned_client


@dataclass(slots=True)
class OllamaLLMRerankerProvider:
    model: str
    remote: RemoteProviderSettings
    model_version: str | None = None
    model_revision: str | None = None
    model_digest: str | None = None
    max_documents: int = 20
    max_document_chars: int = 2_000
    max_output_tokens: int = 512
    shared_client: SharedAsyncHttpClient | None = None
    _owned_client: SharedAsyncHttpClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        shared_client: SharedAsyncHttpClient | None = None,
    ) -> "OllamaLLMRerankerProvider":
        return cls(
            model=settings.model,
            remote=getattr(settings, "remote", RemoteProviderSettings()),
            model_version=getattr(settings, "model_version", None),
            model_revision=getattr(settings, "model_revision", None),
            model_digest=getattr(settings, "model_digest", None),
            max_documents=getattr(settings, "llm_max_documents", 20),
            max_document_chars=getattr(settings, "llm_max_document_chars", 2_000),
            max_output_tokens=getattr(settings, "llm_max_output_tokens", 512),
            shared_client=shared_client,
        )

    def available(self) -> bool:
        return self.remote.base_url is not None

    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            provider="ollama_llm",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
            revision=self.model_revision or self.model_version,
            digest=self.model_digest,
        )

    def health(self) -> ProviderHealth:
        if not self.available():
            return ProviderHealth(
                ready=False,
                provider="ollama_llm",
                capability=RERANKER_CAPABILITY,
                model_name=self.model,
                safe_error_code="provider_endpoint_unconfigured",
            )
        try:
            self._client().request_json("GET", "/api/tags")
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="ollama_llm",
                capability=RERANKER_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )
        return ProviderHealth(
            ready=True,
            provider="ollama_llm",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
        )

    def validate_startup(self) -> None:
        if not self.health().ready:
            raise ProviderError(
                "Configured Ollama reranker model is unavailable.",
                code="provider_unavailable",
            )
        self.rerank("startup validation", ["document"], top_n=1)

    def rerank(self, query_text: str, documents: Sequence[str], *, top_n: int) -> list[float]:
        if not documents or top_n < 1:
            return []

        limited_documents = [
            document[: self.max_document_chars]
            for document in list(documents[: min(top_n, self.max_documents)])
        ]
        prompt_lines = [
            "Score each document for relevance to the query.",
            "Return strict JSON with a top-level 'scores' array.",
            "Each item must contain 'index' and 'score' between 0 and 1.",
            f"Query: {query_text}",
        ]
        for index, document in enumerate(limited_documents):
            prompt_lines.append(f"Document {index}: {document}")

        response = self._client().request_json(
            "POST",
            "/api/generate",
            json_body={
                "model": self.model,
                "prompt": "\n".join(prompt_lines),
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                    "num_predict": self.max_output_tokens,
                },
            },
        )
        payload = response.payload
        if not isinstance(payload, dict):
            _raise_malformed_response("Ollama returned malformed reranker data.")
        raw_response = payload.get("response")
        if not isinstance(raw_response, str):
            _raise_malformed_response("Ollama returned malformed reranker data.")
        try:
            parsed = json.loads(raw_response)
        except ValueError as exc:
            raise ProviderError(
                "Ollama returned malformed reranker data.",
                code="provider_malformed_response",
            ) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("scores"), list):
            _raise_malformed_response("Ollama returned malformed reranker data.")

        scores = [0.0 for _ in limited_documents]
        for item in parsed["scores"]:
            if not isinstance(item, dict):
                _raise_malformed_response("Ollama returned malformed reranker data.")
            index = item.get("index")
            score = item.get("score")
            if not isinstance(index, int) or not isinstance(score, (int, float)):
                _raise_malformed_response("Ollama returned malformed reranker data.")
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
                name=f"ollama-reranker:{self.model}",
                settings=self.remote,
            )
        return self._owned_client
