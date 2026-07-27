"""FastAPI lifespan helpers for managed ByteBox resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from ..observability.logging import log_event, log_exception_event
from .container import ApplicationContainer, ReadinessState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_lifespan(
    *,
    managed_store: Any,
    container: ApplicationContainer | None,
) -> Callable[[Any], AsyncIterator[None]]:
    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        app.state.bytebox_container = container
        app.state.bytebox_store = managed_store
        app.state.bytebox_started_at = _utcnow()
        log_event(
            "application.starting",
            operation="startup",
            component="application",
            outcome="starting",
        )
        if container is not None:
            try:
                container.start()
                app.state.bytebox_started_at = container.started_at or _utcnow()
                log_event(
                    "application.ready",
                    operation="startup",
                    component="application",
                    outcome="success",
                )
            except Exception:
                if container.startup_error is not None:
                    log_exception_event(
                        "application.start_failed",
                        container.startup_error,
                        operation="startup",
                        component="application",
                        outcome="failure",
                    )
                if container.readiness_state is not ReadinessState.FAILED:
                    _close_managed_store(managed_store)
                raise
        else:
            log_event(
                "application.ready",
                operation="startup",
                component="application",
                outcome="success",
            )

        try:
            yield
        finally:
            log_event(
                "application.stopping",
                operation="shutdown",
                component="application",
                outcome="stopping",
            )
            _close_managed_store(managed_store)
            log_event(
                "application.stopped",
                operation="shutdown",
                component="application",
                outcome="success",
            )

    return lifespan


def _close_managed_store(managed_store: Any) -> None:
    closer = getattr(managed_store, "close", None)
    if callable(closer):
        closer()