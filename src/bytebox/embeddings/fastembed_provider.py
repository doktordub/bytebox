"""FastEmbed embedding and reranker adapters with offline model safeguards."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.util import find_spec
import inspect
from math import sqrt
import os
from pathlib import Path
from typing import Any

from ..errors import ProviderError
from .framework import (
    EMBEDDING_CAPABILITY,
    RERANKER_CAPABILITY,
    ModelIdentity,
    ProviderHealth,
)
from .manifests import read_model_manifest, resolve_manifest_path, verify_model_manifest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_text_embedding_class() -> type[Any]:
    from fastembed import TextEmbedding

    return TextEmbedding


def _load_text_cross_encoder_class() -> type[Any]:
    from ..retrieval.rerank import TextCrossEncoder

    if TextCrossEncoder is None:
        raise ProviderError("FastEmbed reranker runtime is not available.", code="provider_unavailable")

    return TextCrossEncoder


def _normalize_vector(values: Sequence[float]) -> list[float]:
    norm = sqrt(sum(float(value) * float(value) for value in values))
    if norm == 0.0:
        return [float(value) for value in values]
    return [float(value) / norm for value in values]


def _sanitize_provider_exception(message: str, *, code: str, exc: BaseException) -> ProviderError:
    return ProviderError(message, code=code)


def _supports_keyword(target: Any, name: str) -> bool:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return True
    if name in signature.parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _filter_kwargs(target: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return kwargs

    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return kwargs

    return {key: value for key, value in kwargs.items() if key in signature.parameters}


def _call_with_supported_kwargs(target: Any, *args: Any, **kwargs: Any) -> Any:
    return target(*args, **_filter_kwargs(target, kwargs))


def _enable_hf_offline_mode() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


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
    model_path: Path | None = None
    cache_dir: Path | None = None
    local_files_only: bool = False
    hf_hub_offline: bool = False
    threads: int | None = None
    execution_providers: tuple[str, ...] = ()
    expected_dim: int | None = 384
    normalize: bool = True
    model_revision: str | None = None
    model_digest: str | None = None
    manifest_path: Path | None = None
    require_manifest: bool = False
    require_checksums: bool = False
    batch_size: int = 64
    _runtime: Any | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Any) -> "FastEmbedProvider":
        return cls(
            model=settings.model,
            model_version=getattr(settings, "model_version", None),
            model_path=getattr(settings, "model_path", None),
            cache_dir=getattr(settings, "cache_dir", None),
            local_files_only=getattr(settings, "local_files_only", False),
            hf_hub_offline=getattr(settings, "hf_hub_offline", False),
            threads=getattr(settings, "threads", None),
            execution_providers=tuple(getattr(settings, "execution_providers", ()) or ()),
            expected_dim=getattr(settings, "dim", None),
            normalize=getattr(settings, "normalize", True),
            model_revision=getattr(settings, "model_revision", None),
            model_digest=getattr(settings, "model_digest", None),
            manifest_path=getattr(settings, "manifest_path", None),
            require_manifest=getattr(settings, "require_manifest", False),
            require_checksums=getattr(settings, "require_checksums", False),
            batch_size=getattr(settings, "batch_size", 64),
        )

    def available(self) -> bool:
        return fastembed_runtime_available()

    def identity(self) -> ModelIdentity:
        manifest = self._load_manifest(strict=False)
        revision = self.model_revision or self.model_version
        digest = self.model_digest
        vector_dimension = self.expected_dim
        normalization = self.normalize
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)

        if manifest is not None:
            revision = manifest.revision or revision
            digest = manifest.digest or digest
            vector_dimension = manifest.vector_dimension or vector_dimension
            normalization = manifest.normalization if manifest.normalization is not None else normalization

        return ModelIdentity(
            provider="fastembed",
            capability=EMBEDDING_CAPABILITY,
            model_name=self.model,
            revision=revision,
            digest=digest,
            vector_dimension=vector_dimension,
            normalization=normalization,
            manifest_path=manifest_path,
        )

    def health(self) -> ProviderHealth:
        try:
            if not self.available():
                raise ProviderError("FastEmbed runtime is not available.", code="provider_unavailable")
            self._validate_runtime_prerequisites()
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="fastembed",
                capability=EMBEDDING_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )

        return ProviderHealth(
            ready=True,
            provider="fastembed",
            capability=EMBEDDING_CAPABILITY,
            model_name=self.model,
        )

    def embed_text(self, text: str) -> EmbeddedText:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[EmbeddedText]:
        if not texts:
            return []

        runtime = self._get_runtime()
        identity = self.identity()
        created_at = _utcnow()

        try:
            raw_vectors = _call_with_supported_kwargs(
                runtime.embed,
                list(texts),
                batch_size=self.batch_size,
            )
            embedded = [self._build_embedded_text(raw_vector, created_at, identity) for raw_vector in raw_vectors]
        except ProviderError:
            raise
        except Exception as exc:
            raise _sanitize_provider_exception(
                "FastEmbed embedding failed.",
                code="provider_embed_failed",
                exc=exc,
            ) from exc

        if len(embedded) != len(texts):
            raise ProviderError(
                "FastEmbed returned a different number of embeddings than requested.",
                code="provider_embed_failed",
            )

        return embedded

    def _build_embedded_text(
        self,
        raw_vector: Sequence[float],
        created_at: datetime,
        identity: ModelIdentity,
    ) -> EmbeddedText:
        vector = [float(value) for value in raw_vector]
        expected_dim = identity.vector_dimension or self.expected_dim
        if expected_dim is not None and len(vector) != expected_dim:
            raise ProviderError(
                "FastEmbed returned an unexpected embedding dimension.",
                code="provider_dimension_mismatch",
            )
        if self.normalize:
            vector = _normalize_vector(vector)
        return EmbeddedText(
            vector=vector,
            model=identity.model_name,
            model_version=identity.persistence_version,
            dim=len(vector),
            created_at=created_at,
        )

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime

        if not self.available():
            raise ProviderError("FastEmbed runtime is not available.", code="provider_unavailable")

        self._validate_runtime_prerequisites()

        try:
            text_embedding_cls = _load_text_embedding_class()
            runtime_kwargs = self._runtime_kwargs(text_embedding_cls)
            self._runtime = text_embedding_cls(**runtime_kwargs)
        except ProviderError:
            raise
        except Exception as exc:
            raise _sanitize_provider_exception(
                "FastEmbed provider initialization failed.",
                code="provider_init_failed",
                exc=exc,
            ) from exc

        return self._runtime

    def _runtime_kwargs(self, constructor: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self._runtime_model_name(),
        }
        if self.cache_dir is not None:
            kwargs["cache_dir"] = self.cache_dir
        if self.local_files_only:
            kwargs["local_files_only"] = True
        if self.threads is not None:
            kwargs["threads"] = self.threads
        if self.execution_providers:
            providers = list(self.execution_providers)
            if _supports_keyword(constructor, "providers"):
                kwargs["providers"] = providers
            elif _supports_keyword(constructor, "execution_providers"):
                kwargs["execution_providers"] = providers
        return _filter_kwargs(constructor, kwargs)

    def _runtime_model_name(self) -> str:
        if self.model_path is not None:
            return str(self.model_path)
        return self.model

    def _validate_runtime_prerequisites(self) -> None:
        if self.hf_hub_offline:
            _enable_hf_offline_mode()

        strict_offline = self.hf_hub_offline or self.local_files_only
        if strict_offline and self.model_path is None:
            raise ProviderError(
                "Configured local model files are unavailable.",
                code="provider_model_missing",
            )

        if self.model_path is not None and not self.model_path.exists():
            raise ProviderError(
                "Configured local model files are unavailable.",
                code="provider_model_missing",
            )

        manifest = self._load_manifest(strict=self.require_manifest or self.require_checksums)
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)
        if manifest is not None and self.model_path is not None and manifest_path is not None:
            verification = verify_model_manifest(
                self.model_path,
                manifest,
                manifest_path=manifest_path,
                require_checksums=self.require_checksums,
            )
            if not verification.ok:
                raise ProviderError(
                    "Configured model files failed verification.",
                    code="provider_model_verification_failed",
                )
        elif self.require_checksums:
            raise ProviderError(
                "Configured model manifest was not found.",
                code="provider_manifest_missing",
            )

    def _load_manifest(self, *, strict: bool) -> Any | None:
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)
        if manifest_path is None or not manifest_path.exists():
            if strict and self.require_manifest:
                raise ProviderError(
                    "Configured model manifest was not found.",
                    code="provider_manifest_missing",
                )
            return None
        return read_model_manifest(manifest_path)


@dataclass(slots=True)
class FastEmbedRerankerProvider:
    model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_version: str | None = None
    model_path: Path | None = None
    cache_dir: Path | None = None
    local_files_only: bool = False
    hf_hub_offline: bool = False
    threads: int | None = None
    execution_providers: tuple[str, ...] = ()
    batch_size: int = 32
    model_revision: str | None = None
    model_digest: str | None = None
    manifest_path: Path | None = None
    require_manifest: bool = False
    require_checksums: bool = False
    _runtime: Any | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Any) -> "FastEmbedRerankerProvider":
        return cls(
            model=settings.model,
            model_version=getattr(settings, "model_version", None),
            model_path=getattr(settings, "model_path", None),
            cache_dir=getattr(settings, "cache_dir", None),
            local_files_only=getattr(settings, "local_files_only", False),
            hf_hub_offline=getattr(settings, "hf_hub_offline", False),
            threads=getattr(settings, "threads", None),
            execution_providers=tuple(getattr(settings, "execution_providers", ()) or ()),
            batch_size=getattr(settings, "batch_size", 32),
            model_revision=getattr(settings, "model_revision", None),
            model_digest=getattr(settings, "model_digest", None),
            manifest_path=getattr(settings, "manifest_path", None),
            require_manifest=getattr(settings, "require_manifest", False),
            require_checksums=getattr(settings, "require_checksums", False),
        )

    def available(self) -> bool:
        return fastembed_reranker_runtime_available()

    def identity(self) -> ModelIdentity:
        manifest = self._load_manifest(strict=False)
        revision = self.model_revision or self.model_version
        digest = self.model_digest
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)
        if manifest is not None:
            revision = manifest.revision or revision
            digest = manifest.digest or digest

        return ModelIdentity(
            provider="fastembed",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
            revision=revision,
            digest=digest,
            manifest_path=manifest_path,
        )

    def health(self) -> ProviderHealth:
        try:
            if not self.available():
                raise ProviderError("FastEmbed reranker runtime is not available.", code="provider_unavailable")
            self._validate_runtime_prerequisites()
        except ProviderError as exc:
            return ProviderHealth(
                ready=False,
                provider="fastembed",
                capability=RERANKER_CAPABILITY,
                model_name=self.model,
                safe_error_code=exc.code,
            )

        return ProviderHealth(
            ready=True,
            provider="fastembed",
            capability=RERANKER_CAPABILITY,
            model_name=self.model,
        )

    def rerank(self, query_text: str, documents: Sequence[str], *, top_n: int) -> list[float]:
        if not documents or top_n < 1:
            return []

        runtime = self._get_runtime()
        limited_documents = list(documents[:top_n])
        try:
            raw_scores = _call_with_supported_kwargs(
                runtime.rerank,
                query_text,
                limited_documents,
                batch_size=min(self.batch_size, len(limited_documents)),
            )
        except Exception as exc:
            raise _sanitize_provider_exception(
                "FastEmbed reranker request failed.",
                code="provider_rerank_failed",
                exc=exc,
            ) from exc

        scores: list[float] = []
        for item in raw_scores:
            score = getattr(item, "score", item)
            scores.append(float(score))
        return scores

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime

        if not self.available():
            raise ProviderError(
                "FastEmbed reranker runtime is not available.",
                code="provider_unavailable",
            )

        self._validate_runtime_prerequisites()

        try:
            text_cross_encoder_cls = _load_text_cross_encoder_class()
            runtime_kwargs = self._runtime_kwargs(text_cross_encoder_cls)
            self._runtime = text_cross_encoder_cls(**runtime_kwargs)
        except ProviderError:
            raise
        except Exception as exc:
            raise _sanitize_provider_exception(
                "FastEmbed reranker initialization failed.",
                code="provider_init_failed",
                exc=exc,
            ) from exc

        return self._runtime

    def _runtime_kwargs(self, constructor: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self._runtime_model_name(),
        }
        if self.cache_dir is not None:
            kwargs["cache_dir"] = self.cache_dir
        if self.local_files_only:
            kwargs["local_files_only"] = True
        if self.threads is not None:
            kwargs["threads"] = self.threads
        if self.execution_providers:
            providers = list(self.execution_providers)
            if _supports_keyword(constructor, "providers"):
                kwargs["providers"] = providers
            elif _supports_keyword(constructor, "execution_providers"):
                kwargs["execution_providers"] = providers
        return _filter_kwargs(constructor, kwargs)

    def _runtime_model_name(self) -> str:
        if self.model_path is not None:
            return str(self.model_path)
        return self.model

    def _validate_runtime_prerequisites(self) -> None:
        if self.hf_hub_offline:
            _enable_hf_offline_mode()

        strict_offline = self.hf_hub_offline or self.local_files_only
        if strict_offline and self.model_path is None:
            raise ProviderError(
                "Configured local model files are unavailable.",
                code="provider_model_missing",
            )

        if self.model_path is not None and not self.model_path.exists():
            raise ProviderError(
                "Configured local model files are unavailable.",
                code="provider_model_missing",
            )

        manifest = self._load_manifest(strict=self.require_manifest or self.require_checksums)
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)
        if manifest is not None and self.model_path is not None and manifest_path is not None:
            verification = verify_model_manifest(
                self.model_path,
                manifest,
                manifest_path=manifest_path,
                require_checksums=self.require_checksums,
            )
            if not verification.ok:
                raise ProviderError(
                    "Configured model files failed verification.",
                    code="provider_model_verification_failed",
                )
        elif self.require_checksums:
            raise ProviderError(
                "Configured model manifest was not found.",
                code="provider_manifest_missing",
            )

    def _load_manifest(self, *, strict: bool) -> Any | None:
        manifest_path = resolve_manifest_path(self.model_path, self.manifest_path)
        if manifest_path is None or not manifest_path.exists():
            if strict and self.require_manifest:
                raise ProviderError(
                    "Configured model manifest was not found.",
                    code="provider_manifest_missing",
                )
            return None
        return read_model_manifest(manifest_path)


def fastembed_runtime_available() -> bool:
    return find_spec("fastembed") is not None


def fastembed_reranker_runtime_available() -> bool:
    if find_spec("fastembed") is None:
        return False
    try:
        _load_text_cross_encoder_class()
    except Exception:
        return False
    return True
