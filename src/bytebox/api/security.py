"""API-layer security helpers, middleware, and TLS utilities."""

from __future__ import annotations

import hmac
import ssl
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Sequence
from uuid import uuid4

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope as AsgiScope, Send

from ..auth import AuthenticatedPrincipal
from ..config import ApiSettings, ApiTlsSettings
from ..errors import AuthenticationError, AuthorizationError
from ..observability import (
    activate_trace_context,
    request_span,
    resolve_trace_context,
    restore_trace_context,
)
from ..observability.context import current_trace_id
from ..observability.logging import log_event

TRACE_ID_HEADER_NAME = "X-Trace-ID"
API_TOKEN_HEADER_NAME = "X-API-Token"
DELETE_CONFIRMATION_HEADER_NAME = "X-Confirm-Delete"
DELETE_CONFIRMATION_VALUE = "hard-delete"
IDEMPOTENCY_KEY_HEADER_NAME = "X-Idempotency-Key"
TRACEPARENT_HEADER_NAME = "traceparent"


def build_api_error_payload(*, code: str, message: str, trace_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "trace_id": trace_id,
        }
    }


def build_api_error_response(
    request: Request | None,
    *,
    status_code: int,
    code: str,
    message: str,
    trace_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    resolved_trace_id = trace_id or request_trace_id(request)
    response_headers = _security_headers()
    response_headers[TRACE_ID_HEADER_NAME] = resolved_trace_id
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content=build_api_error_payload(
            code=code,
            message=message,
            trace_id=resolved_trace_id,
        ),
        headers=response_headers,
    )


def request_trace_id(request: Request | None) -> str:
    trace_id = current_trace_id()
    if trace_id:
        return trace_id
    if request is not None:
        trace_id = getattr(request.state, "bytebox_trace_id", None)
        if isinstance(trace_id, str) and trace_id:
            return trace_id
        header_value = request.headers.get(TRACE_ID_HEADER_NAME)
        if header_value:
            return header_value
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class _ConfiguredToken:
    name: str | None
    token: str
    scopes: frozenset[str]


class ApiAuthorizer:
    def __init__(self, settings: ApiSettings) -> None:
        self._auth_enabled = settings.auth.enabled
        configured_tokens: list[_ConfiguredToken] = []
        for token_settings in settings.auth.tokens:
            configured_tokens.append(
                _ConfiguredToken(
                    name=token_settings.name,
                    token=token_settings.token.get_secret_value(),
                    scopes=frozenset(token_settings.scopes),
                )
            )
        self._tokens = tuple(configured_tokens)

    @property
    def enabled(self) -> bool:
        return self._auth_enabled and bool(self._tokens)

    def dependencies_for(self, *required_scopes: str) -> list[Any]:
        if not required_scopes or not self.enabled:
            return []
        return [Depends(self._build_dependency(required_scopes))]

    def assert_request_scopes(self, request: Request, *required_scopes: str) -> None:
        if not required_scopes or not self.enabled:
            return
        principal = getattr(request.state, "bytebox_principal", None)
        if not isinstance(principal, AuthenticatedPrincipal):
            raise AuthenticationError("Authentication is required.")
        if not principal.has_scopes(required_scopes):
            raise AuthorizationError("The authenticated token does not grant this operation.")

    def _build_dependency(self, required_scopes: Sequence[str]) -> Any:
        async def require_scopes(
            request: Request,
            x_api_token: str | None = Header(default=None, alias=API_TOKEN_HEADER_NAME),
        ) -> None:
            principal = self._authenticate_request(request, x_api_token)
            if not principal.has_scopes(required_scopes):
                raise AuthorizationError("The authenticated token does not grant this operation.")

        return require_scopes

    def _authenticate_request(
        self,
        request: Request,
        supplied_token: str | None,
    ) -> AuthenticatedPrincipal:
        principal = getattr(request.state, "bytebox_principal", None)
        if isinstance(principal, AuthenticatedPrincipal):
            return principal

        if not supplied_token:
            raise AuthenticationError("Authentication is required.")

        for configured in self._tokens:
            if hmac.compare_digest(supplied_token, configured.token):
                principal = AuthenticatedPrincipal(
                    token_name=configured.name,
                    scopes=configured.scopes,
                )
                request.state.bytebox_principal = principal
                return principal

        raise AuthenticationError("Authentication failed.")


class ApiContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        super().__init__(app)
        self._trusted_hosts = tuple(item.lower() for item in settings.trusted_hosts)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        trace_context = resolve_trace_context(
            request.headers.get(TRACEPARENT_HEADER_NAME),
            request.headers.get(TRACE_ID_HEADER_NAME),
        )
        trace_state = activate_trace_context(trace_context)
        trace_id = trace_context.trace_id
        request.state.bytebox_trace_id = trace_id
        request.state.bytebox_traceparent = trace_context.traceparent
        started = perf_counter()

        try:
            host = (request.url.hostname or "").lower()
            if self._trusted_hosts and host and not _host_allowed(host, self._trusted_hosts):
                log_event(
                    "security.configuration_rejected",
                    level="warn",
                    operation="http.request",
                    component="security",
                    outcome="rejected",
                    code="trusted_host_rejected",
                )
                return build_api_error_response(
                    request,
                    status_code=400,
                    code="BYTEBOX_TRUSTED_HOST_REJECTED",
                    message="The request host is not allowed.",
                    trace_id=trace_id,
                )

            otel_enabled = bool(getattr(request.app.state.bytebox_settings.logging, "opentelemetry_enabled", False))
            with request_span(
                "bytebox.http.request",
                headers=request.headers,
                enabled=otel_enabled,
                attributes={
                    "http.method": request.method,
                    "http.target": request.url.path,
                },
            ):
                response = await call_next(request)
            _apply_security_headers(response, trace_id)
            metrics = getattr(request.app.state, "bytebox_metrics", None)
            if metrics is not None:
                metrics.increment(
                    "bytebox_http_requests_total",
                    labels={
                        "method": request.method,
                        "status_family": f"{response.status_code // 100}xx",
                    },
                )
            log_event(
                "request.completed",
                level="debug",
                operation="http.request",
                component="api",
                outcome="success",
                method=request.method,
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started) * 1000.0, 3),
            )
            return response
        finally:
            restore_trace_context(trace_state)


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = _header_from_scope(scope, TRACE_ID_HEADER_NAME) or uuid4().hex
        content_length = _header_from_scope(scope, "content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await _send_limit_response(send, trace_id)
                    return
            except ValueError:
                await _send_limit_response(send, trace_id)
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise _BodyLimitExceeded()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyLimitExceeded:
            log_event(
                "rate_or_limit.rejected",
                level="warn",
                operation="http.request",
                component="security",
                outcome="rejected",
                code="request_too_large",
            )
            await _send_limit_response(send, trace_id)


def build_api_tls_context(settings: ApiTlsSettings) -> ssl.SSLContext | None:
    if not settings.enabled:
        return None

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(
        certfile=str(settings.cert_file),
        keyfile=str(settings.key_file),
        password=(
            settings.key_password.get_secret_value()
            if settings.key_password is not None
            else None
        ),
    )

    if settings.client_ca_file is not None:
        context.load_verify_locations(cafile=str(settings.client_ca_file))

    if settings.require_client_certificate:
        context.verify_mode = ssl.CERT_REQUIRED
    elif settings.client_ca_file is not None:
        context.verify_mode = ssl.CERT_OPTIONAL
    return context


def _host_allowed(host: str, trusted_hosts: Sequence[str]) -> bool:
    if "*" in trusted_hosts:
        return True
    for pattern in trusted_hosts:
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
        if host == pattern:
            return True
    return False


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _apply_security_headers(response: Response, trace_id: str) -> None:
    for key, value in _security_headers().items():
        response.headers.setdefault(key, value)
    response.headers.setdefault(TRACE_ID_HEADER_NAME, trace_id)


def _header_from_scope(scope: AsgiScope, key: str) -> str | None:
    encoded = key.lower().encode("latin-1")
    for raw_key, raw_value in scope.get("headers", []):
        if raw_key.lower() == encoded:
            return raw_value.decode("latin-1")
    return None


async def _send_limit_response(send: Send, trace_id: str) -> None:
    response = JSONResponse(
        status_code=413,
        content=build_api_error_payload(
            code="BYTEBOX_REQUEST_TOO_LARGE",
            message="The request body exceeds the configured limit.",
            trace_id=trace_id,
        ),
        headers={
            **_security_headers(),
            TRACE_ID_HEADER_NAME: trace_id,
        },
    )
    scope: AsgiScope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "client": None,
        "server": None,
    }
    await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class _BodyLimitExceeded(RuntimeError):
    pass