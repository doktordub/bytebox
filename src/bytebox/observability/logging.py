"""Structured logging bootstrap and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Mapping

from .._version import __version__
from ..config import LoggingSettings
from .context import current_trace_id
from .redaction import Redactor

_LOGGER_NAME = "bytebox"
_MANAGED_LOGGERS = (_LOGGER_NAME, "py.warnings", "uvicorn", "uvicorn.access", "uvicorn.error")
_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "off": logging.CRITICAL + 100,
}
_ACTIVE_CONFIG: tuple[str, str, int, bool] | None = None
_ACTIVE_REDACTOR = Redactor()


def configure_logging(settings: LoggingSettings) -> Redactor:
    global _ACTIVE_CONFIG, _ACTIVE_REDACTOR

    fingerprint = (
        settings.level,
        settings.format,
        settings.max_field_length,
        settings.capture_warnings,
    )
    if _ACTIVE_CONFIG == fingerprint:
        return _ACTIVE_REDACTOR

    redactor = Redactor(max_field_length=settings.max_field_length)
    root_logger = logging.getLogger(_LOGGER_NAME)

    for logger_name in _MANAGED_LOGGERS:
        _reset_logger(logging.getLogger(logger_name))

    logging.captureWarnings(False)
    if settings.level == "off":
        for logger_name in _MANAGED_LOGGERS:
            logger = logging.getLogger(logger_name)
            logger.setLevel(_LEVEL_MAP["off"])
            logger.propagate = False
            logger.addHandler(logging.NullHandler())
        _ACTIVE_CONFIG = fingerprint
        _ACTIVE_REDACTOR = redactor
        return redactor

    handler = logging.StreamHandler()
    handler._bytebox_managed = True  # type: ignore[attr-defined]
    handler.setFormatter(_ByteBoxFormatter(structured=(settings.format == "json"), redactor=redactor))
    root_logger.addHandler(handler)
    root_logger.setLevel(_LEVEL_MAP[settings.level])
    root_logger.propagate = False

    if settings.capture_warnings:
        logging.captureWarnings(True)
        warnings_logger = logging.getLogger("py.warnings")
        warnings_logger.handlers = list(root_logger.handlers)
        warnings_logger.setLevel(logging.WARNING)
        warnings_logger.propagate = False

    for logger_name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers = list(root_logger.handlers)
        logger.setLevel(root_logger.level)
        logger.propagate = False

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = list(root_logger.handlers)
    access_logger.setLevel(root_logger.level)
    access_logger.propagate = False

    _ACTIVE_CONFIG = fingerprint
    _ACTIVE_REDACTOR = redactor
    return redactor


def log_event(event: str, *, level: str = "info", **fields: Any) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    level_name = level.lower()
    level_value = _LEVEL_MAP.get(level_name, logging.INFO)
    if not logger.isEnabledFor(level_value):
        return

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "severity": _severity_label(level_value),
        "event": event,
        "service.name": "bytebox",
        "service.version": __version__,
        "trace_id": current_trace_id(),
    }
    payload.update(_ACTIVE_REDACTOR.redact(fields))
    logger.log(level_value, event, extra={"bytebox_event": payload})


def log_exception_event(
    event: str,
    exc: BaseException,
    *,
    level: str = "warn",
    **fields: Any,
) -> None:
    log_event(
        event,
        level=level,
        exception=_ACTIVE_REDACTOR.safe_exception(exc),
        **fields,
    )


def _reset_logger(logger: logging.Logger) -> None:
    logger.handlers = [
        handler for handler in logger.handlers if not getattr(handler, "_bytebox_managed", False)
    ]
    logger.disabled = False


def _severity_label(level_value: int) -> str:
    if level_value == logging.WARNING:
        return "WARN"
    return logging.getLevelName(level_value)


class _ByteBoxFormatter(logging.Formatter):
    def __init__(self, *, structured: bool, redactor: Redactor) -> None:
        super().__init__()
        self._structured = structured
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "bytebox_event", None)
        if not isinstance(payload, Mapping):
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "severity": _severity_label(record.levelno),
                "event": "application.log",
                "service.name": "bytebox",
                "service.version": __version__,
                "trace_id": current_trace_id(),
                "message": self._redactor.redact(record.getMessage()),
            }

        if self._structured:
            return json.dumps(payload, separators=(",", ":"), sort_keys=True)

        context = " ".join(
            f"{key}={value}"
            for key, value in payload.items()
            if key not in {"timestamp", "severity", "event"} and value not in (None, "")
        )
        return f"{payload['timestamp']} {payload['severity']} {payload['event']} {context}".rstrip()