"""Central redaction helpers shared by logs and diagnostics."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import SecretStr

_REDACTED = "[REDACTED]"
_TRUNCATED_SUFFIX = "...[truncated]"
_SENSITIVE_NAME_TOKENS = frozenset(
    {
        "api",
        "apikey",
        "authorization",
        "bearer",
        "cert",
        "cookie",
        "credential",
        "key",
        "password",
        "secret",
        "session",
        "token",
    }
)
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-token",
    }
)
_VALUE_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bsk-[a-z0-9]+\b"),
)


class Redactor:
    def __init__(self, *, max_field_length: int = 256) -> None:
        self.max_field_length = max_field_length

    def redact(self, value: Any, *, field_name: str | None = None) -> Any:
        if field_name is not None and self._is_sensitive_name(field_name):
            return _REDACTED

        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, SecretStr):
            return _REDACTED
        if isinstance(value, BaseException):
            return self.safe_exception(value)
        if isinstance(value, Path):
            return value.name
        if isinstance(value, Mapping):
            return {str(key): self.redact(item, field_name=str(key)) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [self.redact(item) for item in value]
        if hasattr(value, "model_dump") and callable(value.model_dump):
            return self.redact(value.model_dump(mode="python"))

        return self._sanitize_text(str(value), field_name=field_name)

    def sanitize_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        if headers is None:
            return {}
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            if self._is_sensitive_header(key):
                sanitized[str(key)] = _REDACTED
                continue
            sanitized[str(key)] = self._sanitize_text(str(value), field_name=str(key))
        return sanitized

    def safe_exception(self, exc: BaseException) -> dict[str, Any]:
        code = getattr(exc, "code", None)
        return {
            "type": exc.__class__.__name__,
            "code": self._sanitize_text(str(code)) if code else None,
        }

    def _sanitize_text(self, value: str, *, field_name: str | None = None) -> str:
        if field_name is not None and self._is_sensitive_name(field_name):
            return _REDACTED

        text = value.replace("\r", r"\r").replace("\n", r"\n")
        for pattern in _VALUE_PATTERNS:
            text = pattern.sub(_REDACTED, text)
        if len(text) > self.max_field_length:
            text = f"{text[: self.max_field_length]}{_TRUNCATED_SUFFIX}"
        return text

    def _is_sensitive_header(self, value: str) -> bool:
        normalized = self._normalize_name(value)
        return normalized in _SENSITIVE_HEADER_NAMES or self._is_sensitive_name(value)

    def _is_sensitive_name(self, value: str) -> bool:
        normalized = self._normalize_name(value)
        if normalized in _SENSITIVE_HEADER_NAMES:
            return True
        parts = [part for part in normalized.split("_") if part]
        if any(part in _SENSITIVE_NAME_TOKENS for part in parts):
            return True
        return normalized.endswith(("_token", "_secret", "_password", "_key"))

    @staticmethod
    def _normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
