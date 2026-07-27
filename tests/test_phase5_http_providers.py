from __future__ import annotations

import os

import httpx
import pytest
import bytebox.service as service_module

from bytebox.bootstrap.container import ApplicationContainer
from bytebox.config import MemoryStoreSettings, RemoteProviderSettings
from bytebox.embeddings.llamacpp_provider import (
    LlamaCppEmbeddingProvider,
    LlamaCppRerankerProvider,
)
from bytebox.embeddings.ollama_provider import OllamaEmbeddingProvider
from bytebox.embeddings.remote_http import JsonHttpResponse, SharedAsyncHttpClient
from bytebox.errors import ProviderError
from bytebox.service import MemoryService


class _FakeSharedClient:
    def __init__(self, responses: dict[tuple[str, str], list[JsonHttpResponse]]) -> None:
        self._responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[dict[str, object]] = []

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        headers=None,
        expected_statuses=(200,),
    ) -> JsonHttpResponse:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json_body": json_body,
                "headers": headers,
                "expected_statuses": tuple(expected_statuses),
            }
        )
        queue = self._responses[(method, path)]
        return queue.pop(0)


class _FakeRemoteSharedClient:
    created = 0
    closed = 0

    def __init__(self, name: str, settings: object, transport=None, resolver=None) -> None:
        del name, settings, transport, resolver
        type(self).created += 1

    def close(self) -> None:
        type(self).closed += 1


class _FakeRemoteProvider:
    validate_calls = 0
    seen_clients: list[object | None] = []

    def __init__(self, *, shared_client: object | None = None) -> None:
        type(self).seen_clients.append(shared_client)

    @classmethod
    def from_settings(cls, settings, *, shared_client=None):
        del settings
        return cls(shared_client=shared_client)

    def validate_startup(self) -> None:
        type(self).validate_calls += 1

    def close(self) -> None:
        return None


class _FakeContainerService:
    def __init__(self) -> None:
        self.initialize_calls = 0
        self.validate_calls = 0

    def initialize(self, *, warmup_embedding_provider: bool = False, warmup_reranker_provider: bool = False):
        del warmup_embedding_provider, warmup_reranker_provider
        self.initialize_calls += 1
        return self

    def validate_model_providers(self) -> None:
        self.validate_calls += 1

    def close(self) -> None:
        return None

    @property
    def database_handle(self):
        return None

    @property
    def repository(self):
        return None

    @property
    def embedding_provider(self):
        return None

    @property
    def reranker_provider(self):
        return None


def test_shared_async_http_client_blocks_public_endpoints() -> None:
    settings = RemoteProviderSettings(base_url="https://models.example")

    with pytest.raises(ProviderError) as exc_info:
        SharedAsyncHttpClient(
            name="public-endpoint",
            settings=settings,
            resolver=lambda host, port: ("93.184.216.34",),
        )

    assert exc_info.value.code == "provider_endpoint_disallowed"


def test_shared_async_http_client_revalidates_redirect_targets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    settings = RemoteProviderSettings(
        base_url="http://127.0.0.1:11434",
        follow_redirects=True,
    )
    client = SharedAsyncHttpClient(
        name="redirect-check",
        settings=settings,
        transport=httpx.MockTransport(handler),
        resolver=lambda host, port: ("127.0.0.1",) if host == "127.0.0.1" else ("169.254.169.254",),
    )

    try:
        with pytest.raises(ProviderError) as exc_info:
            client.request_json("GET", "/api/tags")
    finally:
        client.close()

    assert exc_info.value.code == "provider_endpoint_disallowed"


def test_ollama_embedding_provider_batches_and_normalizes() -> None:
    fake_client = _FakeSharedClient(
        {
            ("POST", "/api/embed"): [
                JsonHttpResponse(
                    status_code=200,
                    payload={
                        "model": "nomic-embed-text",
                        "embeddings": [
                            [3.0, 4.0, 0.0, 0.0],
                            [0.0, 5.0, 0.0, 0.0],
                        ],
                        "total_duration": 123,
                    },
                    headers={},
                )
            ]
        }
    )
    provider = OllamaEmbeddingProvider(
        model="nomic-embed-text",
        remote=RemoteProviderSettings(base_url="http://127.0.0.1:11434"),
        expected_dim=4,
        normalize=True,
        shared_client=fake_client,
    )

    embedded = provider.embed_batch(["first", "second"])

    assert len(embedded) == 2
    assert embedded[0].vector == [0.6, 0.8, 0.0, 0.0]
    assert embedded[1].vector == [0.0, 1.0, 0.0, 0.0]
    assert provider.identity().provider == "ollama"
    assert fake_client.calls[0]["path"] == "/api/embed"
    assert fake_client.calls[0]["json_body"] == {
        "model": "nomic-embed-text",
        "input": ["first", "second"],
    }


def test_ollama_embedding_provider_sanitizes_malformed_provider_errors() -> None:
    fake_client = _FakeSharedClient(
        {
            ("POST", "/api/embed"): [
                JsonHttpResponse(
                    status_code=200,
                    payload={"error": "Bearer top-secret-token", "embeddings": [[1.0, 2.0, 3.0, 4.0]]},
                    headers={},
                )
            ]
        }
    )
    provider = OllamaEmbeddingProvider(
        model="nomic-embed-text",
        remote=RemoteProviderSettings(base_url="http://127.0.0.1:11434"),
        expected_dim=4,
        shared_client=fake_client,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.embed_batch(["first", "second"])

    assert exc_info.value.code == "provider_malformed_response"
    assert "top-secret-token" not in str(exc_info.value)
    assert "Bearer" not in str(exc_info.value)


def test_llamacpp_embedding_provider_supports_endpoint_aliases() -> None:
    fake_client = _FakeSharedClient(
        {
            ("POST", "/v1/embeddings"): [JsonHttpResponse(status_code=404, payload=None, headers={})],
            ("POST", "/embeddings"): [
                JsonHttpResponse(
                    status_code=200,
                    payload={
                        "data": [
                            {"index": 0, "embedding": [1.0, 2.0, 2.0, 1.0]},
                            {"index": 1, "embedding": [0.0, 3.0, 4.0, 0.0]},
                        ]
                    },
                    headers={},
                )
            ],
        }
    )
    provider = LlamaCppEmbeddingProvider(
        model="bge-small",
        remote=RemoteProviderSettings(base_url="http://127.0.0.1:8080"),
        expected_dim=4,
        shared_client=fake_client,
    )

    embedded = provider.embed_batch(["first", "second"])

    assert [item.dim for item in embedded] == [4, 4]
    assert embedded[0].vector == pytest.approx([0.31622777, 0.63245553, 0.63245553, 0.31622777])
    assert embedded[1].vector == pytest.approx([0.0, 0.6, 0.8, 0.0])
    assert [call["path"] for call in fake_client.calls] == ["/v1/embeddings", "/embeddings"]


def test_llamacpp_reranker_provider_supports_endpoint_aliases() -> None:
    fake_client = _FakeSharedClient(
        {
            ("POST", "/rerank"): [JsonHttpResponse(status_code=404, payload=None, headers={})],
            ("POST", "/v1/rerank"): [
                JsonHttpResponse(
                    status_code=200,
                    payload={
                        "results": [
                            {"index": 1, "relevance_score": 0.2},
                            {"index": 0, "relevance_score": 0.9},
                        ]
                    },
                    headers={},
                )
            ],
        }
    )
    provider = LlamaCppRerankerProvider(
        model="bge-reranker-base",
        remote=RemoteProviderSettings(base_url="http://127.0.0.1:8080"),
        shared_client=fake_client,
    )

    scores = provider.rerank("thin adapters", ["doc a", "doc b"], top_n=2)

    assert scores == [0.9, 0.2]
    assert [call["path"] for call in fake_client.calls] == ["/rerank", "/v1/rerank"]


def test_memory_service_reuses_shared_http_clients_for_remote_provider_validation(monkeypatch) -> None:
    _FakeRemoteSharedClient.created = 0
    _FakeRemoteSharedClient.closed = 0
    _FakeRemoteProvider.validate_calls = 0
    _FakeRemoteProvider.seen_clients.clear()

    monkeypatch.setattr(service_module, "SharedAsyncHttpClient", _FakeRemoteSharedClient)
    monkeypatch.setattr(service_module, "OllamaEmbeddingProvider", _FakeRemoteProvider)
    monkeypatch.setattr(service_module, "LlamaCppRerankerProvider", _FakeRemoteProvider)

    service = MemoryService(
        MemoryStoreSettings(
            embeddings={
                "provider": "ollama",
                "model": "nomic-embed-text",
                "remote": {"base_url": "http://127.0.0.1:11434"},
            },
            reranker={
                "enabled": True,
                "provider": "llamacpp",
                "model": "bge-reranker-base",
                "remote": {"base_url": "http://127.0.0.1:11434"},
            },
        )
    )

    try:
        service.validate_model_providers()
    finally:
        service.close()

    assert _FakeRemoteSharedClient.created == 1
    assert _FakeRemoteSharedClient.closed == 1
    assert _FakeRemoteProvider.validate_calls == 2
    assert len({id(client) for client in _FakeRemoteProvider.seen_clients}) == 1


def test_application_container_runs_provider_validation_only_in_api_mode() -> None:
    api_service = _FakeContainerService()
    api_container = ApplicationContainer(
        settings=MemoryStoreSettings(),
        service=api_service,
        api_mode=True,
    )

    api_container.start()
    api_container.close()

    assert api_service.initialize_calls == 1
    assert api_service.validate_calls == 1

    library_service = _FakeContainerService()
    library_container = ApplicationContainer(
        settings=MemoryStoreSettings(),
        service=library_service,
        api_mode=False,
    )

    library_container.start()
    library_container.close()

    assert library_service.initialize_calls == 1
    assert library_service.validate_calls == 0


@pytest.mark.skipif(
    not (os.getenv("BYTEBOX_TEST_OLLAMA_BASE_URL") and os.getenv("BYTEBOX_TEST_OLLAMA_MODEL")),
    reason="Set BYTEBOX_TEST_OLLAMA_BASE_URL and BYTEBOX_TEST_OLLAMA_MODEL to run Ollama integration.",
)
def test_ollama_embedding_provider_real_integration() -> None:
    provider = OllamaEmbeddingProvider(
        model=os.environ["BYTEBOX_TEST_OLLAMA_MODEL"],
        remote=RemoteProviderSettings(base_url=os.environ["BYTEBOX_TEST_OLLAMA_BASE_URL"]),
    )

    try:
        embedded = provider.embed_text("ByteBox phase 5 integration probe")
    finally:
        provider.close()

    assert embedded.dim > 0
    assert len(embedded.vector) == embedded.dim


@pytest.mark.skipif(
    not (
        os.getenv("BYTEBOX_TEST_LLAMACPP_BASE_URL")
        and os.getenv("BYTEBOX_TEST_LLAMACPP_EMBED_MODEL")
        and os.getenv("BYTEBOX_TEST_LLAMACPP_RERANK_MODEL")
    ),
    reason=(
        "Set BYTEBOX_TEST_LLAMACPP_BASE_URL, BYTEBOX_TEST_LLAMACPP_EMBED_MODEL, and "
        "BYTEBOX_TEST_LLAMACPP_RERANK_MODEL to run llama.cpp integrations."
    ),
)
def test_llamacpp_provider_real_integration() -> None:
    embedding_provider = LlamaCppEmbeddingProvider(
        model=os.environ["BYTEBOX_TEST_LLAMACPP_EMBED_MODEL"],
        remote=RemoteProviderSettings(base_url=os.environ["BYTEBOX_TEST_LLAMACPP_BASE_URL"]),
    )
    reranker_provider = LlamaCppRerankerProvider(
        model=os.environ["BYTEBOX_TEST_LLAMACPP_RERANK_MODEL"],
        remote=RemoteProviderSettings(base_url=os.environ["BYTEBOX_TEST_LLAMACPP_BASE_URL"]),
    )

    try:
        embedded = embedding_provider.embed_text("ByteBox phase 5 integration probe")
        scores = reranker_provider.rerank(
            "phase 5 probe",
            ["ByteBox adds outbound HTTP provider support."],
            top_n=1,
        )
    finally:
        embedding_provider.close()
        reranker_provider.close()

    assert embedded.dim > 0
    assert scores == pytest.approx([scores[0]])
