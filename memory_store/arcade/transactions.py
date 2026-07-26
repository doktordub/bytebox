"""Transaction helpers for repository and batch persistence flows."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from ..errors import MemoryStoreError, PersistenceError

if TYPE_CHECKING:
	from .queries import ArcadeMemoryRepository
	from ..models import MemoryCreate, MemoryRecord, MemoryUpdate

T = TypeVar("T")


@contextmanager
def managed_transaction(database: object, *, enabled: bool = True):
	if not enabled:
		yield database
		return

	try:
		with database.transaction():
			yield database
	except MemoryStoreError:
		raise
	except Exception as exc:
		raise PersistenceError(f"ArcadeDB transaction failed: {exc}") from exc


def run_in_transaction(database: object, operation: Callable[[], T]) -> T:
	with managed_transaction(database):
		return operation()


def batch_insert_memories(
	repository: "ArcadeMemoryRepository",
	memories: Iterable["MemoryCreate"],
) -> list["MemoryRecord"]:
	inserted: list["MemoryRecord"] = []

	def _operation() -> list["MemoryRecord"]:
		for memory in memories:
			inserted.append(repository._insert_memory(memory, use_transaction=False))
		return inserted

	return run_in_transaction(repository.database, _operation)


def batch_update_memories(
	repository: "ArcadeMemoryRepository",
	updates: Sequence[tuple[str, "MemoryUpdate"]],
) -> list["MemoryRecord"]:
	updated: list["MemoryRecord"] = []

	def _operation() -> list["MemoryRecord"]:
		for memory_id, patch in updates:
			updated.append(repository._update_memory(memory_id, patch, use_transaction=False))
		return updated

	return run_in_transaction(repository.database, _operation)
