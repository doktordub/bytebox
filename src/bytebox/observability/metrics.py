"""Minimal in-memory metrics recorder."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from threading import Lock
from typing import Any

_BLOCKED_LABEL_KEYS = frozenset(
    {
        "content",
        "memory_id",
        "path",
        "prompt",
        "query",
        "text",
        "token",
        "trace_id",
    }
)


def _normalize_labels(labels: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if labels is None:
        return ()

    normalized: list[tuple[str, str]] = []
    for key, value in labels.items():
        label_key = str(key).strip().lower()
        if not label_key or label_key in _BLOCKED_LABEL_KEYS:
            continue
        normalized.append((label_key, str(value)))
    return tuple(sorted(normalized))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{value}"' for key, value in labels)
    return f"{{{rendered}}}"


class NoopMetricsRecorder:
    def increment(self, _name: str, *, amount: float = 1.0, labels: Mapping[str, Any] | None = None) -> None:
        del amount, labels

    def set_gauge(self, _name: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        del value, labels

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {}

    def render_openmetrics(self) -> str:
        return ""


class InMemoryMetricsRecorder:
    def __init__(self) -> None:
        self._lock = Lock()
        self._types: dict[str, str] = {}
        self._values: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)

    def increment(self, name: str, *, amount: float = 1.0, labels: Mapping[str, Any] | None = None) -> None:
        self._update(name, metric_type="counter", delta=amount, labels=labels)

    def set_gauge(self, name: str, value: float, *, labels: Mapping[str, Any] | None = None) -> None:
        self._update(name, metric_type="gauge", value=value, labels=labels)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                name: [
                    {
                        "type": self._types[name],
                        "labels": dict(label_key),
                        "value": metric_value,
                    }
                    for label_key, metric_value in sorted(values.items())
                ]
                for name, values in sorted(self._values.items())
            }

    def render_openmetrics(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._values):
                lines.append(f"# TYPE {name} {self._types[name]}")
                for labels, value in sorted(self._values[name].items()):
                    lines.append(f"{name}{_format_labels(labels)} {value}")
        return "\n".join(lines).strip() + ("\n" if lines else "")

    def _update(
        self,
        name: str,
        *,
        metric_type: str,
        labels: Mapping[str, Any] | None,
        delta: float | None = None,
        value: float | None = None,
    ) -> None:
        normalized_labels = _normalize_labels(labels)
        with self._lock:
            self._types.setdefault(name, metric_type)
            metric_values = self._values[name]
            if metric_type == "counter":
                metric_values[normalized_labels] = metric_values.get(normalized_labels, 0.0) + float(delta or 0.0)
            else:
                metric_values[normalized_labels] = float(value or 0.0)