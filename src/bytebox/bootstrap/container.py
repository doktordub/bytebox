"""Application container for managed ByteBox startup and shutdown."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Condition
from time import monotonic
from typing import Any, Iterator

from ..config import MemoryStoreSettings
from ..errors import ConfigError, PersistenceError
from ..observability.logging import configure_logging, log_event
from ..service import MemoryService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReadinessState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(slots=True)
class ApplicationContainer:
    settings: MemoryStoreSettings
    service: MemoryService | None = None
    api_mode: bool = False
    readiness_state: ReadinessState = ReadinessState.NEW
    started_at: datetime | None = None
    last_readiness_change_at: datetime = field(default_factory=_utcnow)
    last_error_code: str | None = None
    last_error_at: datetime | None = None
    database_handle: Any | None = None
    repositories: dict[str, Any] = field(default_factory=dict)
    model_providers: dict[str, Any] = field(default_factory=dict)
    shared_http_clients: dict[str, Any] = field(default_factory=dict)
    telemetry_resources: dict[str, Any] = field(default_factory=dict)
    startup_error: Exception | None = None
    _in_flight_operations: int = 0
    _accepting_operations: bool = False
    _shutdown_grace_period_seconds: float = 1.0
    _condition: Condition = field(default_factory=Condition, repr=False)

    def __post_init__(self) -> None:
        configure_logging(self.settings.logging)
        if self.service is None:
            self.service = MemoryService(self.settings)

    @property
    def is_ready(self) -> bool:
        return self.readiness_state is ReadinessState.READY and self._accepting_operations

    @property
    def in_flight_operations(self) -> int:
        return self._in_flight_operations

    @property
    def accepting_operations(self) -> bool:
        return self._accepting_operations

    def start(self) -> MemoryService:
        if self.service is None:
            self.service = MemoryService(self.settings)
        if self.readiness_state is ReadinessState.READY:
            return self.service
        if self.readiness_state is ReadinessState.STARTING:
            raise PersistenceError("ByteBox startup is already in progress.")
        if self.readiness_state is ReadinessState.STOPPING:
            raise PersistenceError("ByteBox is shutting down and cannot accept startup work.")

        self.startup_error = None
        self._set_readiness_state(ReadinessState.STARTING)
        self._accepting_operations = False

        try:
            self._validate_worker_configuration()
            self.service.initialize()
            if self.api_mode:
                self.service.validate_model_providers()
            self.refresh_runtime_view()
        except Exception as exc:
            self.startup_error = exc
            self.last_error_code = exc.__class__.__name__
            self.last_error_at = _utcnow()
            self._accepting_operations = False
            self._teardown_resources()
            self._set_readiness_state(ReadinessState.FAILED)
            raise

        self.last_error_code = None
        self.last_error_at = None
        self.started_at = _utcnow()
        self._set_readiness_state(ReadinessState.READY)
        self._accepting_operations = True
        return self.service

    def refresh_runtime_view(self) -> None:
        if self.service is None:
            self.database_handle = None
            self.repositories = {}
            self.model_providers = {}
            return

        self.database_handle = self.service.database_handle
        repository = self.service.repository
        self.repositories = {"memory": repository} if repository is not None else {}

        embedding_provider = self.service.embedding_provider
        reranker_provider = self.service.reranker_provider
        providers: dict[str, Any] = {}
        if embedding_provider is not None:
            providers["embeddings"] = embedding_provider
        if reranker_provider is not None:
            providers["reranker"] = reranker_provider
        self.model_providers = providers
        self.shared_http_clients = dict(self.service.shared_http_clients)

    @contextmanager
    def operation(self, _name: str) -> Iterator[None]:
        with self._condition:
            if not self.is_ready:
                raise PersistenceError("ByteBox is not ready to accept operations.")
            self._in_flight_operations += 1
        try:
            yield
        finally:
            with self._condition:
                self._in_flight_operations -= 1
                if self._in_flight_operations == 0:
                    self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._accepting_operations = False
            if self.readiness_state is ReadinessState.READY:
                self._set_readiness_state(ReadinessState.STOPPING)
            deadline = monotonic() + self._shutdown_grace_period_seconds
            while self._in_flight_operations > 0:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

        self._teardown_resources()
        self._set_readiness_state(ReadinessState.CLOSED)

    def _teardown_resources(self) -> None:
        self.database_handle = None
        self.repositories = {}
        self.model_providers = {}
        self.shared_http_clients = {}
        self.telemetry_resources = {}
        if self.service is not None:
            self.service.close()

    def _validate_worker_configuration(self) -> None:
        if (
            self.api_mode
            and self.settings.database.embedded_single_process
            and self.settings.api.workers > 1
        ):
            raise ConfigError("Embedded ByteBox mode requires api.workers == 1.")

    def _set_readiness_state(self, state: ReadinessState) -> None:
        previous = self.readiness_state
        if previous is state:
            return
        self.readiness_state = state
        self.last_readiness_change_at = _utcnow()
        if previous is not ReadinessState.NEW:
            log_event(
                "health.readiness.changed",
                level="warn" if state in {ReadinessState.FAILED, ReadinessState.STOPPING} else "info",
                operation="lifecycle",
                component="container",
                previous=previous.value,
                current=state.value,
                outcome="state_change",
            )