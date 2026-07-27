"""Request-scoped trace context helpers."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any, Iterator, Mapping
from uuid import uuid4

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TRACE_ID = ContextVar("bytebox_trace_id", default=None)
_TRACEPARENT = ContextVar("bytebox_traceparent", default=None)


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    traceparent: str


@dataclass(frozen=True, slots=True)
class _TraceContextState:
    previous_trace_id: str | None
    previous_traceparent: str | None


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def current_traceparent() -> str | None:
    return _TRACEPARENT.get()


def set_current_traceparent(traceparent: str | None) -> None:
    _TRACEPARENT.set(traceparent)


def get_current_traceparent() -> str | None:
    return _TRACEPARENT.get()


def normalize_trace_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not _TRACE_ID_RE.fullmatch(candidate):
        return None
    if candidate == "0" * 32:
        return None
    return candidate


def build_traceparent(trace_id: str | None = None) -> str:
    resolved_trace_id = normalize_trace_id(trace_id) or uuid4().hex
    return f"00-{resolved_trace_id}-{uuid4().hex[:16]}-01"


def resolve_trace_context(
    traceparent_header: str | None,
    trace_id_header: str | None,
) -> TraceContext:
    if isinstance(traceparent_header, str):
        candidate = traceparent_header.strip().lower()
        match = _TRACEPARENT_RE.fullmatch(candidate)
        if match is not None:
            return TraceContext(
                trace_id=match.group("trace_id"),
                traceparent=candidate,
            )

    trace_id = normalize_trace_id(trace_id_header) or uuid4().hex
    return TraceContext(trace_id=trace_id, traceparent=build_traceparent(trace_id))


def activate_trace_context(trace_context: TraceContext) -> object:
    previous_trace_id = current_trace_id()
    previous_traceparent = current_traceparent()
    _TRACE_ID.set(trace_context.trace_id)
    _TRACEPARENT.set(trace_context.traceparent)
    return _TraceContextState(previous_trace_id, previous_traceparent)


def restore_trace_context(state: object) -> None:
    if not isinstance(state, _TraceContextState):
        return
    _TRACE_ID.set(state.previous_trace_id)
    _TRACEPARENT.set(state.previous_traceparent)


def request_span(
    name: str,
    *,
    headers: Mapping[str, str],
    enabled: bool,
    attributes: Mapping[str, Any] | None = None,
):
    if not enabled:
        return nullcontext(None)

    try:
        from opentelemetry import trace
        from opentelemetry.context import attach, detach
        from opentelemetry.propagate import extract
    except ModuleNotFoundError:
        return nullcontext(None)

    carrier = {str(key): str(value) for key, value in headers.items()}
    extracted_context = extract(carrier)
    tracer = trace.get_tracer("bytebox")

    @contextmanager
    def _span() -> Iterator[Any]:
        token = attach(extracted_context)
        try:
            with tracer.start_as_current_span(name) as span:
                for key, value in (attributes or {}).items():
                    span.set_attribute(key, value)
                yield span
        finally:
            detach(token)

    return _span()