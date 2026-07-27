"""Shared outbound HTTP transport for remote embedding and reranker providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import ipaddress
import socket
import ssl
from threading import BoundedSemaphore, Lock
from time import monotonic, sleep
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

from anyio.from_thread import start_blocking_portal
import httpx

from ..config import RemoteProviderSettings
from ..errors import ProviderError
from ..observability.context import get_current_traceparent, set_current_traceparent
from ..observability.logging import log_event

_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_BLOCKED_METADATA_IPS = frozenset({"100.100.100.200", "169.254.169.254"})


def _generate_traceparent() -> str:
    return f"00-{uuid4().hex}-{uuid4().hex[:16]}-01"


def _redacted_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))


def _rooted_url(base_url: str, path: str) -> str:
    root = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(root, path.lstrip("/"))


def _is_absolute_url(value: str) -> bool:
    lower = value.lower()
    return lower.startswith("http://") or lower.startswith("https://")


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status_code: int
    payload: Any | None
    headers: Mapping[str, str]


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int
    reset_after_seconds: float
    _failure_count: int = 0
    _opened_at: float | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def before_request(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if (monotonic() - self._opened_at) >= self.reset_after_seconds:
                self._opened_at = None
                self._failure_count = 0
                return
        raise ProviderError(
            "Remote provider is temporarily unavailable.",
            code="provider_circuit_open",
        )

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._opened_at = monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            opened = self._opened_at is not None and (monotonic() - self._opened_at) < self.reset_after_seconds
            return {
                "state": "open" if opened else "closed",
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
            }


@dataclass(slots=True)
class EndpointPolicy:
    settings: RemoteProviderSettings
    resolver: Callable[[str, int], Sequence[str]] | None = None
    _base_url: str = field(init=False, repr=False)
    _base_host: str = field(init=False, repr=False)
    _base_addresses: tuple[str, ...] = field(init=False, repr=False)
    _allowed_networks: tuple[ipaddress._BaseNetwork, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        base_url = self.settings.base_url
        if base_url is None:
            raise ProviderError(
                "Remote provider endpoint is not configured.",
                code="provider_endpoint_unconfigured",
            )
        parts = urlsplit(base_url)
        self._validate_url_parts(parts)
        self._allowed_networks = tuple(
            ipaddress.ip_network(item, strict=False) for item in self.settings.allowed_cidrs
        )
        self._base_url = self._normalize_url(parts)
        self._base_host = (parts.hostname or "").lower()
        port = parts.port or self._default_port(parts.scheme)
        self._base_addresses = tuple(sorted(self._resolve_host(self._base_host, port)))
        self._validate_addresses(self._base_host, self._base_addresses)

    @property
    def base_url(self) -> str:
        return self._base_url

    def build_url(self, path: str) -> str:
        if _is_absolute_url(path):
            return self.validate_url(path, enforce_rebinding=False)
        return _rooted_url(self._base_url, path)

    def validate_url(self, url: str, *, enforce_rebinding: bool) -> str:
        parts = urlsplit(url)
        self._validate_url_parts(parts)
        normalized = self._normalize_url(parts)
        host = (parts.hostname or "").lower()
        port = parts.port or self._default_port(parts.scheme)
        addresses = tuple(sorted(self._resolve_host(host, port)))
        self._validate_addresses(host, addresses)
        if enforce_rebinding and host == self._base_host and addresses != self._base_addresses:
            raise ProviderError(
                "Remote provider endpoint changed during execution.",
                code="provider_endpoint_rebound",
            )
        return normalized

    def validate_redirect(self, current_url: str, location: str) -> str:
        redirected_url = urljoin(current_url, location)
        return self.validate_url(redirected_url, enforce_rebinding=False)

    def _resolve_host(self, host: str, port: int) -> tuple[str, ...]:
        if self.resolver is not None:
            return tuple(self.resolver(host, port))
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ProviderError(
                "Remote provider endpoint could not be resolved.",
                code="provider_endpoint_resolution_failed",
            ) from exc
        addresses = sorted({info[4][0] for info in infos if info[4]})
        if not addresses:
            raise ProviderError(
                "Remote provider endpoint could not be resolved.",
                code="provider_endpoint_resolution_failed",
            )
        return tuple(addresses)

    def _validate_url_parts(self, parts: Any) -> None:
        if parts.scheme not in {"http", "https"}:
            raise ProviderError(
                "Remote provider endpoint scheme is not allowed.",
                code="provider_endpoint_invalid",
            )
        if getattr(parts, "username", None) or getattr(parts, "password", None):
            raise ProviderError(
                "Remote provider URLs must not embed credentials.",
                code="provider_endpoint_invalid",
            )
        if not parts.hostname:
            raise ProviderError(
                "Remote provider endpoint is invalid.",
                code="provider_endpoint_invalid",
            )

    def _validate_addresses(self, host: str, addresses: Sequence[str]) -> None:
        host_allowed = host in self.settings.allowed_hosts
        for address in addresses:
            ip_address = ipaddress.ip_address(address)
            explicitly_allowed = host_allowed or self._address_in_allowed_cidrs(ip_address)
            if str(ip_address) in _BLOCKED_METADATA_IPS and not self.settings.allow_metadata_address:
                raise ProviderError(
                    "Remote provider endpoint is not allowed.",
                    code="provider_endpoint_disallowed",
                )
            if ip_address.is_multicast or ip_address.is_unspecified:
                raise ProviderError(
                    "Remote provider endpoint is not allowed.",
                    code="provider_endpoint_disallowed",
                )
            if ip_address.is_link_local and not self.settings.allow_link_local and not explicitly_allowed:
                raise ProviderError(
                    "Remote provider endpoint is not allowed.",
                    code="provider_endpoint_disallowed",
                )
            if ip_address.is_loopback:
                if self.settings.allow_loopback or explicitly_allowed:
                    continue
                raise ProviderError(
                    "Remote provider endpoint is not allowed.",
                    code="provider_endpoint_disallowed",
                )
            if explicitly_allowed:
                continue
            if ip_address.is_private and self.settings.allow_private_network:
                continue
            raise ProviderError(
                "Remote provider endpoint is not allowed.",
                code="provider_endpoint_disallowed",
            )

    def _address_in_allowed_cidrs(self, address: ipaddress._BaseAddress) -> bool:
        return any(address in network for network in self._allowed_networks)

    @staticmethod
    def _default_port(scheme: str) -> int:
        return 443 if scheme == "https" else 80

    @staticmethod
    def _normalize_url(parts: Any) -> str:
        host = parts.hostname or ""
        netloc = host
        if parts.port is not None:
            netloc = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


@dataclass(slots=True)
class SharedAsyncHttpClient:
    name: str
    settings: RemoteProviderSettings
    transport: Any | None = None
    resolver: Callable[[str, int], Sequence[str]] | None = None
    _policy: EndpointPolicy = field(init=False, repr=False)
    _breaker: CircuitBreaker = field(init=False, repr=False)
    _semaphore: BoundedSemaphore = field(init=False, repr=False)
    _portal: Any | None = field(default=None, init=False, repr=False)
    _portal_cm: Any | None = field(default=None, init=False, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _active_requests: int = field(default=0, init=False, repr=False)
    _request_successes: int = field(default=0, init=False, repr=False)
    _request_errors: int = field(default=0, init=False, repr=False)
    _total_duration_ms: float = field(default=0.0, init=False, repr=False)
    _last_duration_ms: float = field(default=0.0, init=False, repr=False)
    _stats_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._policy = EndpointPolicy(self.settings, resolver=self.resolver)
        self._breaker = CircuitBreaker(
            failure_threshold=self.settings.circuit_breaker_failures,
            reset_after_seconds=self.settings.circuit_breaker_reset_seconds,
        )
        self._semaphore = BoundedSemaphore(self.settings.max_concurrency)
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        self._client = self._portal.call(self._build_client)

    @property
    def base_url(self) -> str:
        return self._policy.base_url

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
        expected_statuses: Sequence[int] = (200,),
    ) -> JsonHttpResponse:
        started = monotonic()
        self._breaker.before_request()
        acquired = self._semaphore.acquire(timeout=self.settings.timeout_pool_seconds)
        if not acquired:
            raise ProviderError(
                "Remote provider concurrency limit was reached.",
                code="provider_concurrency_limited",
            )
        self._change_active_requests(1)

        try:
            attempts = self.settings.max_retries + 1
            last_error: ProviderError | None = None
            for attempt in range(attempts):
                try:
                    response = self._request_once(
                        method,
                        path,
                        json_body=json_body,
                        headers=headers,
                    )
                except ProviderError as exc:
                    last_error = exc
                    if self._is_retryable_error(exc) and attempt < attempts - 1:
                        self._breaker.record_failure()
                        sleep(self.settings.retry_backoff_seconds * (2 ** attempt))
                        continue
                    self._breaker.record_failure()
                    raise

                if response.is_redirect:
                    self._breaker.record_failure()
                    raise ProviderError(
                        "Remote provider redirected unexpectedly.",
                        code="provider_redirect_disallowed",
                    )

                if response.status_code not in expected_statuses:
                    if (
                        response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt < attempts - 1
                    ):
                        self._breaker.record_failure()
                        sleep(self.settings.retry_backoff_seconds * (2 ** attempt))
                        continue
                    self._breaker.record_failure()
                    raise ProviderError(
                        "Remote provider request failed.",
                        code=f"provider_http_status_{response.status_code}",
                    )

                payload = self._decode_payload(response)
                self._breaker.record_success()
                duration_ms = round((monotonic() - started) * 1000.0, 3)
                self._record_request(success=True, duration_ms=duration_ms)
                log_event(
                    "provider.request.completed",
                    level="info",
                    operation="provider.request",
                    component="remote_provider",
                    provider=self.name.partition(":")[0],
                    model=self.name.partition(":")[2] or None,
                    outcome="success",
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                return JsonHttpResponse(
                    status_code=response.status_code,
                    payload=payload,
                    headers=dict(response.headers),
                )

            if last_error is not None:
                raise last_error
            raise ProviderError(
                "Remote provider request failed.",
                code="provider_transport_failed",
            )
        except ProviderError as exc:
            duration_ms = round((monotonic() - started) * 1000.0, 3)
            self._record_request(success=False, duration_ms=duration_ms)
            log_event(
                "provider.request.failed",
                level="warn",
                operation="provider.request",
                component="remote_provider",
                provider=self.name.partition(":")[0],
                model=self.name.partition(":")[2] or None,
                outcome="failure",
                safe_error_code=exc.code,
                duration_ms=duration_ms,
            )
            raise
        finally:
            self._change_active_requests(-1)
            self._semaphore.release()

    def close(self) -> None:
        client = self._client
        portal = self._portal
        portal_cm = self._portal_cm
        self._client = None
        self._portal = None
        self._portal_cm = None

        if client is not None and portal is not None:
            portal.call(client.aclose)
        if portal_cm is not None:
            portal_cm.__exit__(None, None, None)

    def diagnostics(self) -> dict[str, Any]:
        with self._stats_lock:
            average_duration_ms = (
                self._total_duration_ms / (self._request_successes + self._request_errors)
                if (self._request_successes + self._request_errors) > 0
                else 0.0
            )
            return {
                "name": self.name,
                "circuit": self._breaker.snapshot(),
                "active_requests": self._active_requests,
                "request_successes": self._request_successes,
                "request_errors": self._request_errors,
                "last_duration_ms": round(self._last_duration_ms, 3),
                "average_duration_ms": round(average_duration_ms, 3),
                "max_concurrency": self.settings.max_concurrency,
            }

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        if self._client is None or self._portal is None:
            raise ProviderError(
                "Remote provider client is unavailable.",
                code="provider_transport_failed",
            )

        current_url = self._policy.build_url(path)
        redirects_remaining = self.settings.max_redirects
        merged_headers = self._merged_headers(headers)

        while True:
            validated_url = self._policy.validate_url(current_url, enforce_rebinding=True)
            try:
                response = self._portal.call(
                    self._send_request,
                    method,
                    validated_url,
                    merged_headers,
                    json_body,
                )
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    "Remote provider request timed out.",
                    code="provider_timeout",
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "Remote provider transport failed.",
                    code="provider_transport_failed",
                ) from exc

            if not response.is_redirect or not self.settings.follow_redirects:
                return response
            if redirects_remaining <= 0:
                raise ProviderError(
                    "Remote provider redirected unexpectedly.",
                    code="provider_redirect_disallowed",
                )
            location = response.headers.get("location")
            if not location:
                raise ProviderError(
                    "Remote provider redirected unexpectedly.",
                    code="provider_redirect_disallowed",
                )
            current_url = self._policy.validate_redirect(validated_url, location)
            redirects_remaining -= 1

    async def _send_request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Any | None,
    ) -> httpx.Response:
        if self._client is None:
            raise ProviderError(
                "Remote provider client is unavailable.",
                code="provider_transport_failed",
            )
        return await self._client.request(method, url, headers=dict(headers), json=json_body)

    def _build_client(self) -> httpx.AsyncClient:
        verify: ssl.SSLContext | bool
        if self._policy.base_url.startswith("https://"):
            verify = self._build_ssl_context()
        else:
            verify = self.settings.verify_tls
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.settings.timeout_connect_seconds,
                read=self.settings.timeout_read_seconds,
                write=self.settings.timeout_write_seconds,
                pool=self.settings.timeout_pool_seconds,
            ),
            limits=httpx.Limits(
                max_connections=self.settings.max_connections,
                max_keepalive_connections=self.settings.max_keepalive_connections,
            ),
            trust_env=self.settings.trust_env,
            follow_redirects=False,
            verify=verify,
            transport=self.transport,
        )

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self.settings.verify_tls:
            cafile = (
                str(self.settings.ca_bundle_path)
                if self.settings.ca_bundle_path is not None
                else None
            )
            context = ssl.create_default_context(cafile=cafile)
        else:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        if self.settings.client_cert_path is not None:
            context.load_cert_chain(
                certfile=str(self.settings.client_cert_path),
                keyfile=(
                    str(self.settings.client_key_path)
                    if self.settings.client_key_path is not None
                    else None
                ),
                password=self.settings.client_key_password,
            )
        return context

    def _merged_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        merged = dict(headers or {})
        if not any(key.lower() == "traceparent" for key in merged):
            merged["traceparent"] = get_current_traceparent() or _generate_traceparent()
        return merged

    def _change_active_requests(self, delta: int) -> None:
        with self._stats_lock:
            self._active_requests = max(self._active_requests + delta, 0)

    def _record_request(self, *, success: bool, duration_ms: float) -> None:
        with self._stats_lock:
            if success:
                self._request_successes += 1
            else:
                self._request_errors += 1
            self._last_duration_ms = duration_ms
            self._total_duration_ms += duration_ms

    @staticmethod
    def _decode_payload(response: httpx.Response) -> Any | None:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            safe_url = _redacted_url(str(response.request.url))
            raise ProviderError(
                f"Remote provider returned malformed JSON from {safe_url}.",
                code="provider_malformed_response",
            ) from exc

    @staticmethod
    def _is_retryable_error(exc: ProviderError) -> bool:
        return exc.code in {
            "provider_timeout",
            "provider_transport_failed",
            "provider_circuit_open",
        }
