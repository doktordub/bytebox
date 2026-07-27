"""Embedding providers, registries, manifests, and validation helpers."""

from .fastembed_provider import (
    EmbeddedText,
    FastEmbedProvider,
    FastEmbedRerankerProvider,
    fastembed_reranker_runtime_available,
    fastembed_runtime_available,
)
from .framework import (
    EMBEDDING_CAPABILITY,
    RERANKER_CAPABILITY,
    EmbeddingProvider,
    ModelIdentity,
    ProviderHealth,
    ProviderRegistry,
    RerankerProvider,
)
from .llamacpp_provider import LlamaCppEmbeddingProvider, LlamaCppRerankerProvider
from .manifests import (
    DEFAULT_MODEL_MANIFEST_NAME,
    ModelManifest,
    build_model_manifest_from_directory,
    read_model_manifest,
    resolve_manifest_path,
    verify_model_manifest,
    write_model_manifest,
)
from .model_management import (
    doctor_models,
    export_model_manifest,
    inspect_model,
    install_model,
    list_models,
    verify_models,
)
from .ollama_provider import OllamaEmbeddingProvider, OllamaLLMRerankerProvider
from .remote_http import JsonHttpResponse, SharedAsyncHttpClient, get_current_traceparent, set_current_traceparent
from .text_builder import build_embedding_text
from .validation import (
	read_active_index_dimension,
	validate_active_index_dimension,
	validate_embedding_dimensions,
)

__all__ = [
	"DEFAULT_MODEL_MANIFEST_NAME",
	"EMBEDDING_CAPABILITY",
	"EmbeddedText",
	"EmbeddingProvider",
	"FastEmbedProvider",
	"FastEmbedRerankerProvider",
	"JsonHttpResponse",
	"LlamaCppEmbeddingProvider",
	"LlamaCppRerankerProvider",
	"ModelIdentity",
	"ModelManifest",
	"OllamaEmbeddingProvider",
	"OllamaLLMRerankerProvider",
	"ProviderHealth",
	"ProviderRegistry",
	"RERANKER_CAPABILITY",
	"RerankerProvider",
	"SharedAsyncHttpClient",
	"build_embedding_text",
	"build_model_manifest_from_directory",
	"doctor_models",
	"export_model_manifest",
	"fastembed_reranker_runtime_available",
	"fastembed_runtime_available",
	"get_current_traceparent",
	"inspect_model",
	"install_model",
	"list_models",
	"read_model_manifest",
	"read_active_index_dimension",
	"resolve_manifest_path",
	"set_current_traceparent",
	"validate_active_index_dimension",
	"validate_embedding_dimensions",
	"verify_model_manifest",
	"verify_models",
	"write_model_manifest",
]
