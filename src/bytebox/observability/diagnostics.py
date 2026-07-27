"""Safe health, status, state, and metrics builders."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
from shutil import disk_usage
from typing import Any
from uuid import uuid4

from .._version import __version__
from ..bootstrap import ApplicationContainer, ReadinessState
from ..config import MemoryStoreSettings
from ..models import (
    HealthReadiness,
    HealthStatus,
    HealthStateReport,
    HealthStatusReport,
    HealthLiveness,
    ReadinessCheck,
)


def build_liveness_report() -> HealthLiveness:
    return HealthLiveness(
        version=__version__,
        trace_id=_trace_id(),
    )


def build_readiness_report(
    *,
    store: Any,
    settings: MemoryStoreSettings,
    container: ApplicationContainer | None,
) -> HealthReadiness:
    health = _resolve_health(store)
    service = getattr(container, "service", None)
    external_store = container is None or service is None
    checks = [
        _database_check(health, container),
        _schema_check(health, settings),
        _storage_check(settings),
        _provider_check(
            name="embedding_provider",
            required=not external_store,
            provider=_resolve_provider(service, "embedding_provider", "_embedding_provider"),
            fallback_ready=health.dependencies.get("fastembed") if health is not None else None,
            disable_when_missing=external_store,
        ),
        _provider_check(
            name="reranker_provider",
            required=settings.reranker.enabled and not external_store,
            provider=_resolve_provider(service, "reranker_provider", "_reranker_provider"),
            fallback_ready=True if not settings.reranker.enabled else None,
            disable_when_missing=external_store,
        ),
    ]

    overall_ready = all(
        check.status == "ready"
        for check in checks
        if check.required
    )
    return HealthReadiness(
        status="ready" if overall_ready else "not_ready",
        checks=checks,
        trace_id=_trace_id(),
    )


def build_status_report(
    *,
    store: Any,
    settings: MemoryStoreSettings,
    container: ApplicationContainer | None,
) -> HealthStatusReport:
    health = _resolve_health(store)
    service = getattr(container, "service", None)
    providers = []
    for capability, provider in (
        ("embeddings", _resolve_provider(service, "embedding_provider", "_embedding_provider")),
        ("reranker", _resolve_provider(service, "reranker_provider", "_reranker_provider")),
    ):
        if provider is None:
            if capability == "reranker" and not settings.reranker.enabled:
                continue
            providers.append(
                {
                    "capability": capability,
                    "configured": False,
                }
            )
            continue

        identity = getattr(provider, "identity", lambda: None)()
        providers.append(
            {
                "capability": capability,
                "configured": True,
                "provider": getattr(identity, "provider", None),
                "model": getattr(identity, "model_name", None),
                "revision": getattr(identity, "revision", None),
                "dimension": getattr(identity, "vector_dimension", None),
                "initialized": getattr(service, f"{capability[:-1]}_provider", None) is not None,
            }
        )

    database_state = "unknown"
    schema_version = settings.database.schema_version
    if container is not None and container.database_handle is not None:
        database_state = "open" if container.database_handle.database.is_open() else "closed"
    if health is not None:
        schema_version = health.schema_version

    uptime_seconds = _uptime_seconds(container)
    return HealthStatusReport(
        status=_readiness_label(container, health),
        service="bytebox",
        version=__version__,
        build_commit=settings.application.build_commit,
        build_time=settings.application.build_time,
        uptime_seconds=uptime_seconds,
        environment=settings.application.environment,
        schema_version=schema_version,
        database={
            "state": database_state,
            "embedded_single_process": settings.database.embedded_single_process,
            "create_if_missing": settings.database.create_if_missing,
        },
        providers=providers,
        tls={
            "enabled": settings.api.tls.enabled,
            "require_client_certificate": settings.api.tls.require_client_certificate,
        },
        logging={
            "level": settings.logging.level,
            "format": settings.logging.format,
            "opentelemetry_enabled": settings.logging.opentelemetry_enabled,
        },
        jobs={
            "pending_migrations": 0,
            "pending_reembedding_jobs": 0,
        },
        trace_id=_trace_id(),
    )


def build_state_report(
    *,
    store: Any,
    settings: MemoryStoreSettings,
    container: ApplicationContainer | None,
    metrics: Any | None,
) -> HealthStateReport:
    health = _resolve_health(store)
    stats = store.stats()
    storage_path = _storage_probe_path(settings.database.path)
    writable = os.access(storage_path, os.W_OK)
    free_space_category = "unknown"
    if storage_path.exists():
        try:
            free_space = disk_usage(storage_path).free
            free_space_category = (
                "ok"
                if free_space >= settings.application.minimum_free_space_bytes
                else "low"
            )
        except OSError:
            free_space_category = "unknown"

    provider_states = []
    for name, client in sorted((container.shared_http_clients if container is not None else {}).items()):
        diagnostics = getattr(client, "diagnostics", lambda: {"name": name})()
        provider_states.append(diagnostics)

    return HealthStateReport(
        status=_readiness_label(container, health),
        trace_id=_trace_id(),
        counters={
            "total_records": stats.total_records,
            "global_records": stats.scope_counts.get("global", 0),
            "scoped_records": stats.scope_counts.get("scoped", 0),
        },
        memory_status_counts=stats.status_counts,
        memory_type_counts=stats.type_counts,
        queue={
            "accepting_operations": bool(container.accepting_operations) if container is not None else True,
            "in_flight_operations": int(container.in_flight_operations) if container is not None else 0,
            "workers": settings.api.workers,
        },
        providers=provider_states,
        storage={
            "writable": writable,
            "free_space": free_space_category,
        },
        recent_error={
            "code": getattr(container, "last_error_code", None) if container is not None else None,
            "time": getattr(container, "last_error_at", None) if container is not None else None,
        },
        metrics=(metrics.snapshot() if metrics is not None else {}),
    )


def build_metrics_payload(
    *,
    settings: MemoryStoreSettings,
    container: ApplicationContainer | None,
    metrics: Any,
) -> str:
    lines = [metrics.render_openmetrics().rstrip()] if metrics is not None else []
    uptime_seconds = _uptime_seconds(container)
    lines.extend(
        [
            "# TYPE bytebox_uptime_seconds gauge",
            f"bytebox_uptime_seconds {uptime_seconds}",
            "# TYPE bytebox_inflight_operations gauge",
            f"bytebox_inflight_operations {float(container.in_flight_operations if container is not None else 0)}",
        ]
    )

    current_state = (container.readiness_state.value if container is not None else "ready")
    for state_name in ("new", "starting", "ready", "stopping", "failed", "closed"):
        value = 1.0 if state_name == current_state else 0.0
        lines.append(f'bytebox_readiness_state{{state="{state_name}"}} {value}')
    lines.append(f'bytebox_metrics_enabled {1.0 if settings.api.metrics_enabled else 0.0}')
    return "\n".join(line for line in lines if line) + "\n"


def _database_check(
    health: HealthStatus | None,
    container: ApplicationContainer | None,
) -> ReadinessCheck:
    ready = False
    if health is not None:
        ready = health.status == "ok"
    elif container is not None and container.database_handle is not None:
        ready = container.database_handle.database.is_open()

    return ReadinessCheck(
        name="database",
        status="ready" if ready else "not_ready",
        code="OK" if ready else "DATABASE_UNAVAILABLE",
    )


def _schema_check(
    health: HealthStatus | None,
    settings: MemoryStoreSettings,
) -> ReadinessCheck:
    schema_version = health.schema_version if health is not None else None
    ready = schema_version == settings.database.schema_version
    return ReadinessCheck(
        name="schema",
        status="ready" if ready else "not_ready",
        code="OK" if ready else "SCHEMA_MISMATCH",
        metadata={"expected": settings.database.schema_version, "actual": schema_version},
    )


def _storage_check(settings: MemoryStoreSettings) -> ReadinessCheck:
    target = _storage_probe_path(settings.database.path)
    writable = os.access(target, os.W_OK)
    try:
        free = disk_usage(target).free
    except OSError:
        free = None

    if free is None:
        status = "ready" if writable else "not_ready"
        code = "OK" if writable else "STORAGE_UNWRITABLE"
        free_space = "unknown"
    else:
        enough_space = free >= settings.application.minimum_free_space_bytes
        status = "ready" if writable and enough_space else "not_ready"
        code = "OK" if writable and enough_space else ("STORAGE_LOW" if writable else "STORAGE_UNWRITABLE")
        free_space = "ok" if enough_space else "low"

    return ReadinessCheck(
        name="storage",
        status=status,
        code=code,
        metadata={"writable": writable, "free_space": free_space},
    )


def _provider_check(
    *,
    name: str,
    required: bool,
    provider: Any | None,
    fallback_ready: bool | None,
    disable_when_missing: bool = False,
) -> ReadinessCheck:
    if provider is None:
        if disable_when_missing:
            return ReadinessCheck(name=name, status="disabled", code="EXTERNAL_STORE", required=False)
        if not required:
            return ReadinessCheck(name=name, status="disabled", code="DISABLED", required=False)
        if fallback_ready is not None:
            return ReadinessCheck(
                name=name,
                status="ready" if fallback_ready else "not_ready",
                code="OK" if fallback_ready else "PROVIDER_UNAVAILABLE",
            )
        return ReadinessCheck(name=name, status="not_ready", code="PROVIDER_UNAVAILABLE")

    try:
        health = provider.health()
    except Exception as exc:
        code = getattr(exc, "code", None) or exc.__class__.__name__.upper()
        return ReadinessCheck(name=name, status="not_ready", code=str(code))

    metadata = {
        "provider": health.provider,
        "model": health.model_name,
    }
    return ReadinessCheck(
        name=name,
        status="ready" if health.ready else "not_ready",
        code="OK" if health.ready else (health.safe_error_code or "PROVIDER_UNAVAILABLE"),
        required=required,
        metadata=metadata,
    )


def _resolve_health(store: Any) -> HealthStatus | None:
    try:
        return store.health()
    except Exception:
        return None


def _resolve_provider(service: Any | None, property_name: str, builder_name: str) -> Any | None:
    if service is None:
        return None
    provider = getattr(service, property_name, None)
    if provider is not None:
        return provider
    builder = getattr(service, builder_name, None)
    if callable(builder):
        try:
            return builder()
        except Exception:
            return None
    return None


def _storage_probe_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.exists():
        return candidate
    if candidate.parent.exists():
        return candidate.parent
    return Path.cwd()


def _readiness_label(
    container: ApplicationContainer | None,
    health: HealthStatus | None,
) -> str:
    if container is not None:
        return container.readiness_state.value
    if health is not None:
        return "ready" if health.status == "ok" else "not_ready"
    return "unknown"


def _uptime_seconds(container: ApplicationContainer | None) -> float:
    started_at = getattr(container, "started_at", None)
    if not isinstance(started_at, datetime):
        return 0.0
    return max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)


def _trace_id() -> str:
    from .context import current_trace_id

    return current_trace_id() or uuid4().hex