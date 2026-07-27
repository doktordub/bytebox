"""Authentication and authorization primitives for ByteBox."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class AuthScope(StrEnum):
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_INGEST = "memory:ingest"
    MEMORY_EXPORT = "memory:export"
    MEMORY_IMPORT = "memory:import"
    MEMORY_DELETE = "memory:delete"
    ADMIN_READ = "admin:read"
    ADMIN_OPERATE = "admin:operate"


ALL_AUTH_SCOPES = tuple(scope.value for scope in AuthScope)
_AUTH_SCOPE_SET = frozenset(ALL_AUTH_SCOPES)


def normalize_auth_scopes(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        value = str(scope).strip()
        if not value:
            continue
        if value not in _AUTH_SCOPE_SET:
            supported = ", ".join(ALL_AUTH_SCOPES)
            raise ValueError(f"Unsupported auth scope '{value}'. Expected one of: {supported}.")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    token_name: str | None
    scopes: frozenset[str]

    def has_scopes(self, required_scopes: Iterable[str]) -> bool:
        return set(required_scopes).issubset(self.scopes)