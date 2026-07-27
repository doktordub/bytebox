"""Temporary import-compatibility shim for one ByteBox transition release."""

from __future__ import annotations

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "bytebox")]

from .config import ByteBoxSettings, MemoryStoreSettings
from .models import HealthStatus, MemoryCreate, MemoryRecord, MemorySearchQuery, Scope
from .store import ByteBox, MemoryStore

__all__ = [
    "ByteBox",
    "ByteBoxSettings",
    "HealthStatus",
    "MemoryCreate",
    "MemoryRecord",
    "MemorySearchQuery",
    "MemoryStore",
    "MemoryStoreSettings",
    "Scope",
]

__version__ = "0.1.0"
