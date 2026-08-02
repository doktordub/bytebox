"""Repository helpers that encapsulate ArcadeDB access details."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..errors import MemoryNotFoundError, PersistenceError
from ..models import (
	MemoryCreate,
	MemoryRecord,
	MemoryStatus,
	MemoryType,
	MemoryUpdate,
	Scope,
	SensitivityLevel,
)
from ..retrieval.filters import HardFilter, NormalizedQuery
from .schema import EDGE_TYPES, FULL_TEXT_INDEX_FIELDS, MEMORY_RECORD_VERTEX
from .transactions import managed_transaction

_NULLABLE_FULL_TEXT_FIELDS = tuple(
	field_name for field_name in FULL_TEXT_INDEX_FIELDS if field_name not in {"text", "tags_text"}
)


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


class ArcadeMemoryRepository:
	"""Encapsulates persistence operations for memory records."""

	def __init__(self, database: Any, *, schema_version: int) -> None:
		self._database = database
		self._schema_version = schema_version

	@property
	def database(self) -> Any:
		return self._database

	def insert_memory(self, memory: MemoryCreate | MemoryRecord) -> MemoryRecord:
		return self._insert_memory(memory, use_transaction=True)

	def get_memory(self, memory_id: str) -> MemoryRecord | None:
		record = self._lookup_vertex(memory_id)
		if record is None:
			return None
		return self._hydrate_record(record.to_dict())

	def update_memory(self, memory_id: str, patch: MemoryUpdate) -> MemoryRecord:
		return self._update_memory(memory_id, patch, use_transaction=True)

	def upsert_memory(self, memory: MemoryCreate, stable_key: str | None = None) -> MemoryRecord:
		resolved_stable_key = stable_key or memory.stable_key
		if resolved_stable_key is None:
			return self.insert_memory(memory)

		existing = self.get_by_stable_key(resolved_stable_key)
		if existing is None:
			return self.insert_memory(memory.model_copy(update={"stable_key": resolved_stable_key}))

		merged = self._replace_record(existing, memory, stable_key=resolved_stable_key)
		return self._persist_existing(merged, use_transaction=True)

	def get_by_stable_key(self, stable_key: str) -> MemoryRecord | None:
		rows = self._database.query(
			"sql",
			f"SELECT FROM {MEMORY_RECORD_VERTEX} WHERE stable_key = ? LIMIT 1",
			stable_key,
		)
		first = rows.first()
		if first is None:
			return None
		return self._hydrate_record(first.to_dict())

	def list_by_scope(self, scope: Scope) -> list[MemoryRecord]:
		return self._query_records(*self._select_where(*self._scope_where(scope)))

	def search_vector_candidates(
		self,
		*,
		hard_filter: HardFilter,
		query_vector: Sequence[float],
		top_n: int,
		oversample: int = 4,
	) -> list[MemoryRecord]:
		if top_n < 1:
			return []

		index = self._database.schema.get_vector_index(MEMORY_RECORD_VERTEX, "embedding")
		if index is None:
			return []

		allowed_rids = self._query_rids(
			*self._filtered_select_where(
				hard_filter,
				projection="@rid as rid",
				extra_where=["embedding IS NOT NULL"],
			)
		)
		if not allowed_rids:
			return []

		candidate_limit = min(len(allowed_rids), max(top_n, top_n * max(1, oversample)))
		neighbors = index.find_nearest(query_vector, k=candidate_limit, allowed_rids=allowed_rids)
		return [self._hydrate_record(record.to_dict()) for record, _distance in neighbors]

	def search_full_text_candidates(
		self,
		*,
		hard_filter: HardFilter,
		query: NormalizedQuery,
		top_n: int,
		oversample: int = 4,
	) -> list[MemoryRecord]:
		if top_n < 1 or not query.tokens:
			return []

		sql, params = self._full_text_candidate_query(
			hard_filter=hard_filter,
			query=query,
			top_n=top_n,
			oversample=oversample,
		)
		return self._query_records(sql, params)

	def explain_full_text_candidate_query(
		self,
		*,
		hard_filter: HardFilter,
		query: NormalizedQuery,
		top_n: int,
		oversample: int = 4,
	) -> str:
		if top_n < 1 or not query.tokens:
			return ""

		sql, params = self._full_text_candidate_query(
			hard_filter=hard_filter,
			query=query,
			top_n=top_n,
			oversample=oversample,
		)
		rows = list(self._database.query("sql", f"EXPLAIN {sql}", *params))
		if not rows:
			return ""
		plan = rows[0].get("executionPlanAsString")
		return "" if plan is None else str(plan)

	def list_matching_scope(self, scope: Scope | None = None) -> list[MemoryRecord]:
		if scope is None:
			return self._query_records(f"SELECT FROM {MEMORY_RECORD_VERTEX}", [])
		return self._query_records(*self._select_where(*self._scope_filter_where(scope)))

	def list_by_chunk_id(self, chunk_id: str) -> list[MemoryRecord]:
		return self._query_records(
			f"SELECT FROM {MEMORY_RECORD_VERTEX} WHERE chunk_id = ?",
			[chunk_id],
		)

	def get_chunk_by_id(self, chunk_id: str, *, scope: Scope | None = None) -> MemoryRecord | None:
		query, params = self._select_document_chunk_where(["chunk_id = ?"], [chunk_id], scope=scope)
		records = self._query_records(query, params)
		if not records:
			return None
		return records[0]

	def list_by_source_path(
		self,
		source_path: str,
		*,
		scope: Scope | None = None,
		memory_type: MemoryType | None = None,
	) -> list[MemoryRecord]:
		where_parts = ["source_path LIKE ?"]
		args: list[Any] = [source_path]
		if memory_type is not None:
			where_parts.append("memory_type = ?")
			args.append(memory_type.value)
		if scope is not None:
			scope_clause, scope_args = self._scope_where(scope)
			where_parts.extend(scope_clause)
			args.extend(scope_args)

		query, params = self._select_where(where_parts, args)
		if memory_type == MemoryType.DOCUMENT_CHUNK:
			query = self._append_document_chunk_order(query)
		records = self._query_records(query, params)
		return [record for record in records if record.source_path == source_path]

	def list_chunks_by_source_path(
		self,
		source_path: str,
		*,
		scope: Scope | None = None,
	) -> list[MemoryRecord]:
		query, params = self._select_document_chunk_where(
			["source_path LIKE ?"],
			[source_path],
			scope=scope,
		)
		records = self._query_records(query, params)
		return [record for record in records if record.source_path == source_path]

	def list_chunk_window(
		self,
		source_path: str,
		*,
		scope: Scope,
		document_chunk_index: int,
		before: int,
		after: int,
	) -> list[MemoryRecord]:
		window_before = max(before, 0)
		window_after = max(after, 0)
		lower_bound = max(document_chunk_index - window_before, 0)
		upper_bound = max(document_chunk_index + window_after, lower_bound)
		query, params = self._select_document_chunk_where(
			[
				"source_path LIKE ?",
				"document_chunk_index >= ?",
				"document_chunk_index <= ?",
			],
			[source_path, lower_bound, upper_bound],
			scope=scope,
		)
		records = self._query_records(query, params)
		return [record for record in records if record.source_path == source_path]

	def list_by_source_hash(self, source_hash: str) -> list[MemoryRecord]:
		return self._query_records(
			f"SELECT FROM {MEMORY_RECORD_VERTEX} WHERE source_hash = ?",
			[source_hash],
		)

	def delete_memory(self, memory_id: str) -> None:
		self._delete_memory(memory_id, use_transaction=True)

	def replace_memory(self, record: MemoryRecord) -> MemoryRecord:
		return self._persist_existing(record, use_transaction=True)

	def create_edge(
		self,
		from_memory_id: str,
		to_memory_id: str,
		edge_type: str,
		properties: Mapping[str, Any] | None = None,
	) -> None:
		self._create_edge(
			from_memory_id,
			to_memory_id,
			edge_type,
			properties=properties,
			use_transaction=True,
		)

	def _create_edge(
		self,
		from_memory_id: str,
		to_memory_id: str,
		edge_type: str,
		properties: Mapping[str, Any] | None = None,
		*,
		use_transaction: bool,
	) -> None:
		if edge_type not in EDGE_TYPES:
			raise PersistenceError(f"Unsupported edge type: {edge_type}")

		source = self._lookup_vertex(from_memory_id)
		target = self._lookup_vertex(to_memory_id)
		if source is None or target is None:
			raise MemoryNotFoundError(
				"Both source and target memories must exist before creating an edge."
			)

		edge_properties = dict(properties or {})
		edge_properties.setdefault("created_at", _utcnow())

		with managed_transaction(self._database, enabled=use_transaction):
			edge = source.modify().new_edge(edge_type, target, **edge_properties)
			edge.save()

	def read_one_hop_neighbors(
		self,
		memory_id: str,
		*,
		edge_types: Sequence[str] | None = None,
	) -> list[MemoryRecord]:
		return [
			neighbor
			for _edge_type, neighbor in self.read_one_hop_links(memory_id, edge_types=edge_types)
		]

	def read_one_hop_links(
		self,
		memory_id: str,
		*,
		edge_types: Sequence[str] | None = None,
	) -> list[tuple[str, MemoryRecord]]:
		record = self._lookup_vertex(memory_id)
		if record is None:
			raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")

		labels = tuple(edge_types or EDGE_TYPES)
		neighbors: list[tuple[str, MemoryRecord]] = []
		seen: set[tuple[str, str]] = set()

		for label in labels:
			for edge in record.get_both_edges(label):
				outgoing = edge.get_out()
				incoming = edge.get_in()
				neighbor = incoming if outgoing.get("memory_id") == memory_id else outgoing
				hydrated = self._hydrate_record(neighbor.to_dict())
				key = (label, hydrated.memory_id)
				if key in seen:
					continue
				seen.add(key)
				neighbors.append((label, hydrated))

		return neighbors

	def mark_status(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
		return self.update_memory(memory_id, MemoryUpdate(status=status))

	def count_memories(
		self,
		*,
		scope: Scope | None = None,
		status: MemoryStatus | None = None,
		memory_type: MemoryType | None = None,
	) -> int:
		where_parts: list[str] = []
		args: list[Any] = []

		if scope is not None:
			scope_clause, scope_args = self._scope_where(scope)
			where_parts.extend(scope_clause)
			args.extend(scope_args)
		if status is not None:
			where_parts.append("status = ?")
			args.append(status.value)
		if memory_type is not None:
			where_parts.append("memory_type = ?")
			args.append(memory_type.value)

		query, params = self._select_where(where_parts, args, projection="count(*) as count")
		result = self._database.query("sql", query, *params).first()
		if result is None:
			return 0
		return int(result.get("count") or 0)

	def aggregate_inventory_summary(
		self,
		*,
		include_document_chunks: bool = True,
	) -> dict[str, Any]:
		where_parts, args = self._inventory_where(
			include_document_chunks=include_document_chunks,
		)
		total_records = self._count_matching_rows(where_parts, args)
		global_records = self._count_matching_rows(
			[
				*where_parts,
				"user_id IS NULL",
				"project_id IS NULL",
				"agent_id IS NULL",
			],
			args,
		)
		return {
			"total_records": total_records,
			"scope_counts": {
				"global": global_records,
				"scoped": max(total_records - global_records, 0),
			},
			"status_counts": self._group_counts("status", where_parts=where_parts, args=args),
			"type_counts": self._group_counts("memory_type", where_parts=where_parts, args=args),
		}

	def aggregate_inventory_scopes(
		self,
		*,
		include_names: bool,
		names_limit: int,
		include_document_chunks: bool = True,
	) -> dict[str, Any]:
		where_parts, args = self._inventory_where(
			include_document_chunks=include_document_chunks,
		)
		total_records = self._count_matching_rows(where_parts, args)
		global_records = self._count_matching_rows(
			[
				*where_parts,
				"user_id IS NULL",
				"project_id IS NULL",
				"agent_id IS NULL",
			],
			args,
		)
		return {
			"distinct_scope_tuples": self._count_distinct_scope_tuples(where_parts, args),
			"global_records": global_records,
			"scoped_records": max(total_records - global_records, 0),
			"user_ids": self._aggregate_scope_dimension(
				"user_id",
				include_names=include_names,
				names_limit=names_limit,
				where_parts=where_parts,
				args=args,
			),
			"project_ids": self._aggregate_scope_dimension(
				"project_id",
				include_names=include_names,
				names_limit=names_limit,
				where_parts=where_parts,
				args=args,
			),
			"agent_ids": self._aggregate_scope_dimension(
				"agent_id",
				include_names=include_names,
				names_limit=names_limit,
				where_parts=where_parts,
				args=args,
			),
		}

	def aggregate_inventory_memory_types(
		self,
		*,
		include_document_chunks: bool = True,
	) -> list[dict[str, Any]]:
		where_parts, args = self._inventory_where(
			include_document_chunks=include_document_chunks,
		)
		type_counts = self._group_counts("memory_type", where_parts=where_parts, args=args)
		inventory: list[dict[str, Any]] = []
		for memory_type, count in sorted(type_counts.items()):
			type_where = [*where_parts, "memory_type = ?"]
			type_args = [*args, memory_type]
			global_records = self._count_matching_rows(
				[
					*type_where,
					"user_id IS NULL",
					"project_id IS NULL",
					"agent_id IS NULL",
				],
				type_args,
			)
			oldest_created_at, newest_updated_at = self._aggregate_timestamp_bounds(
				type_where,
				type_args,
			)
			inventory.append(
				{
					"memory_type": memory_type,
					"count": count,
					"status_counts": self._group_counts(
						"status",
						where_parts=type_where,
						args=type_args,
					),
					"scope_counts": {
						"global": global_records,
						"scoped": max(count - global_records, 0),
					},
					"oldest_created_at": oldest_created_at,
					"newest_updated_at": newest_updated_at,
				}
			)
		return inventory

	def aggregate_stats(self) -> dict[str, Any]:
		return self.aggregate_inventory_summary(include_document_chunks=True)

	def _insert_memory(
		self,
		memory: MemoryCreate | MemoryRecord,
		*,
		use_transaction: bool,
	) -> MemoryRecord:
		record = memory if isinstance(memory, MemoryRecord) else self._new_record(memory)

		with managed_transaction(self._database, enabled=use_transaction):
			vertex = self._database.new_vertex(MEMORY_RECORD_VERTEX)
			self._apply_properties(vertex, self._serialize_record(record))
			vertex.save()

		return record

	def _update_memory(
		self,
		memory_id: str,
		patch: MemoryUpdate,
		*,
		use_transaction: bool,
	) -> MemoryRecord:
		existing = self.get_memory(memory_id)
		if existing is None:
			raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")

		patch_values = patch.model_dump(exclude_unset=True, mode="python")
		merged = MemoryRecord.model_validate(
			{
				**existing.model_dump(mode="python"),
				**patch_values,
				"memory_id": existing.memory_id,
				"created_at": existing.created_at,
				"updated_at": _utcnow(),
				"version": existing.version + 1,
			}
		)
		return self._persist_existing(merged, use_transaction=use_transaction)

	def _persist_existing(self, record: MemoryRecord, *, use_transaction: bool) -> MemoryRecord:
		existing = self._lookup_vertex(record.memory_id)
		if existing is None:
			raise MemoryNotFoundError(f"Memory record was not found: {record.memory_id}")

		with managed_transaction(self._database, enabled=use_transaction):
			mutable = existing.modify()
			self._apply_properties(mutable, self._serialize_record(record))
			mutable.save()

		return record

	def _delete_memory(self, memory_id: str, *, use_transaction: bool) -> None:
		existing = self._lookup_vertex(memory_id)
		if existing is None:
			raise MemoryNotFoundError(f"Memory record was not found: {memory_id}")

		with managed_transaction(self._database, enabled=use_transaction):
			existing.delete()

	def _lookup_vertex(self, memory_id: str) -> Any | None:
		try:
			return self._database.lookup_by_key(MEMORY_RECORD_VERTEX, ["memory_id"], [memory_id])
		except Exception as exc:
			raise PersistenceError(f"Failed to lookup memory {memory_id}: {exc}") from exc

	def _new_record(self, memory: MemoryCreate) -> MemoryRecord:
		now = _utcnow()
		payload = memory.model_dump(mode="python")
		payload.update(
			{
				"memory_id": str(uuid4()),
				"created_at": now,
				"updated_at": now,
				"schema_version": self._schema_version,
				"version": 1,
			}
		)
		return MemoryRecord.model_validate(payload)

	def _replace_record(
		self,
		existing: MemoryRecord,
		replacement: MemoryCreate,
		*,
		stable_key: str,
	) -> MemoryRecord:
		payload = existing.model_dump(mode="python")
		payload.update(replacement.model_dump(mode="python"))
		payload.update(
			{
				"memory_id": existing.memory_id,
				"stable_key": stable_key,
				"created_at": existing.created_at,
				"updated_at": _utcnow(),
				"version": existing.version + 1,
				"schema_version": self._schema_version,
			}
		)
		return MemoryRecord.model_validate(payload)

	def _query_records(self, query: str, params: list[Any]) -> list[MemoryRecord]:
		results = self._database.query("sql", query, *params)
		return [self._hydrate_record(row.to_dict()) for row in results]

	def _query_rids(self, query: str, params: list[Any]) -> list[Any]:
		results = self._database.query("sql", query, *params)
		return [row.get("rid") for row in results if row.get("rid") is not None]

	def _query_scalar(self, query: str, *params: Any) -> int:
		result = self._database.query("sql", query, *params).first()
		if result is None:
			return 0
		return int(result.get("count") or 0)

	def _count_matching_rows(self, where_parts: Sequence[str], args: Sequence[Any]) -> int:
		query, params = self._select_where(
			list(where_parts),
			list(args),
			projection="count(*) as count",
		)
		return self._query_scalar(query, *params)

	def _group_counts(
		self,
		field_name: str,
		*,
		where_parts: Sequence[str] | None = None,
		args: Sequence[Any] | None = None,
	) -> dict[str, int]:
		query, params = self._select_where(
			list(where_parts or []),
			list(args or []),
			projection=f"{field_name} as group_key, count(*) as count",
		)
		results = self._database.query("sql", f"{query} GROUP BY {field_name}", *params)
		counts: dict[str, int] = {}
		for row in results:
			key = row.get("group_key")
			if key in {None, ""}:
				continue
			counts[str(key)] = int(row.get("count") or 0)
		return counts

	def _inventory_where(self, *, include_document_chunks: bool) -> tuple[list[str], list[Any]]:
		if include_document_chunks:
			return [], []
		return ["memory_type <> ?"], [MemoryType.DOCUMENT_CHUNK.value]

	def _count_distinct_scope_tuples(self, where_parts: Sequence[str], args: Sequence[Any]) -> int:
		query, params = self._select_where(
			list(where_parts),
			list(args),
			projection="user_id, project_id, agent_id",
		)
		results = self._database.query(
			"sql",
			f"{query} GROUP BY user_id, project_id, agent_id",
			*params,
		)
		return sum(1 for _ in results)

	def _aggregate_scope_dimension(
		self,
		field_name: str,
		*,
		include_names: bool,
		names_limit: int,
		where_parts: Sequence[str],
		args: Sequence[Any],
	) -> dict[str, Any]:
		values = self._distinct_group_values(field_name, where_parts=where_parts, args=args)
		if not include_names:
			return {
				"count": len(values),
				"names": [],
				"truncated": False,
				"remaining": 0,
			}
		names = values[:names_limit]
		remaining = max(len(values) - len(names), 0)
		return {
			"count": len(values),
			"names": names,
			"truncated": remaining > 0,
			"remaining": remaining,
		}

	def _distinct_group_values(
		self,
		field_name: str,
		*,
		where_parts: Sequence[str],
		args: Sequence[Any],
	) -> list[str]:
		query, params = self._select_where(
			[*where_parts, f"{field_name} IS NOT NULL"],
			list(args),
			projection=f"{field_name} as group_key",
		)
		results = self._database.query(
			"sql",
			f"{query} GROUP BY {field_name} ORDER BY {field_name} ASC",
			*params,
		)
		return [
			str(row.get("group_key"))
			for row in results
			if row.get("group_key") not in {None, ""}
		]

	def _aggregate_timestamp_bounds(
		self,
		where_parts: Sequence[str],
		args: Sequence[Any],
	) -> tuple[Any | None, Any | None]:
		query, params = self._select_where(
			list(where_parts),
			list(args),
			projection="min(created_at) as oldest_created_at, max(updated_at) as newest_updated_at",
		)
		result = self._database.query("sql", query, *params).first()
		if result is None:
			return None, None
		return result.get("oldest_created_at"), result.get("newest_updated_at")

	def _filtered_select_where(
		self,
		hard_filter: HardFilter,
		*,
		projection: str = "*",
		extra_where: list[str] | None = None,
	) -> tuple[str, list[Any]]:
		where_parts, args = self._hard_filter_where(hard_filter)
		if extra_where:
			where_parts.extend(extra_where)
		return self._select_where(where_parts, args, projection=projection)

	def _hard_filter_where(self, hard_filter: HardFilter) -> tuple[list[str], list[Any]]:
		where_parts, args = self._scope_where(hard_filter.scope)
		self._append_value_filter(where_parts, args, "memory_type", hard_filter.memory_types)
		self._append_value_filter(where_parts, args, "status", hard_filter.statuses)
		self._append_value_filter(where_parts, args, "source_type", hard_filter.source_types)
		if hard_filter.sensitivity is None:
			where_parts.append("sensitivity <> ?")
			args.append(SensitivityLevel.SENSITIVE.value)
		else:
			where_parts.append("sensitivity = ?")
			args.append(hard_filter.sensitivity.value)
		if hard_filter.allow_retrieval_only:
			where_parts.append("allow_retrieval = true")
		return where_parts, args

	def _append_value_filter(
		self,
		where_parts: list[str],
		args: list[Any],
		field_name: str,
		values: Sequence[Any] | None,
	) -> None:
		if not values:
			return
		normalized = sorted((getattr(value, "value", value) for value in values), key=str)
		if len(normalized) == 1:
			where_parts.append(f"{field_name} = ?")
			args.append(normalized[0])
			return
		where_parts.append("(" + " OR ".join(f"{field_name} = ?" for _ in normalized) + ")")
		args.extend(normalized)

	def _full_text_candidate_query(
		self,
		*,
		hard_filter: HardFilter,
		query: NormalizedQuery,
		top_n: int,
		oversample: int,
	) -> tuple[str, list[Any]]:
		field_terms = [f"{field_name} containsText ?" for field_name in FULL_TEXT_INDEX_FIELDS]
		text_clauses: list[str] = []
		text_args: list[Any] = []
		for term in dict.fromkeys(query.tokens):
			text_clauses.append("(" + " OR ".join(field_terms) + ")")
			text_args.extend([term] * len(FULL_TEXT_INDEX_FIELDS))

		where_parts, args = self._hard_filter_where(hard_filter)
		where_parts.append("(" + " OR ".join(text_clauses) + ")")
		sql, params = self._select_where(where_parts, args + text_args)
		limit = max(top_n, top_n * max(1, oversample))
		return f"{sql} LIMIT {limit}", params

	def _select_where(
		self,
		where_parts: list[str],
		args: list[Any],
		*,
		projection: str = "*",
	) -> tuple[str, list[Any]]:
		if projection == "*":
			query = f"SELECT FROM {MEMORY_RECORD_VERTEX}"
		else:
			query = f"SELECT {projection} FROM {MEMORY_RECORD_VERTEX}"
		if where_parts:
			query += " WHERE " + " AND ".join(where_parts)
		return query, args

	def _select_document_chunk_where(
		self,
		where_parts: list[str],
		args: list[Any],
		*,
		scope: Scope | None = None,
	) -> tuple[str, list[Any]]:
		chunk_where = list(where_parts)
		chunk_args = list(args)
		chunk_where.append("memory_type = ?")
		chunk_args.append(MemoryType.DOCUMENT_CHUNK.value)
		if scope is not None:
			scope_clause, scope_args = self._scope_where(scope)
			chunk_where.extend(scope_clause)
			chunk_args.extend(scope_args)
		query, params = self._select_where(chunk_where, chunk_args)
		return self._append_document_chunk_order(query), params

	def _append_document_chunk_order(self, query: str) -> str:
		return (
			query
			+ " ORDER BY document_chunk_index ASC, section_index ASC, "
			+ "section_chunk_index ASC, memory_id ASC"
		)

	def _scope_where(self, scope: Scope) -> tuple[list[str], list[Any]]:
		where_parts: list[str] = []
		args: list[Any] = []
		for field_name in ("user_id", "project_id", "agent_id"):
			value = getattr(scope, field_name)
			if value is None:
				where_parts.append(f"{field_name} IS NULL")
			else:
				where_parts.append(f"{field_name} = ?")
				args.append(value)
		return where_parts, args

	def _scope_filter_where(self, scope: Scope) -> tuple[list[str], list[Any]]:
		if scope.is_global:
			return self._scope_where(scope)

		where_parts: list[str] = []
		args: list[Any] = []
		for field_name in ("user_id", "project_id", "agent_id"):
			value = getattr(scope, field_name)
			if value is None:
				continue
			where_parts.append(f"{field_name} = ?")
			args.append(value)
		return where_parts, args

	def _serialize_record(self, record: MemoryRecord) -> dict[str, Any]:
		payload = record.model_dump(mode="python", exclude={"scope"})
		payload.pop("content", None)
		payload["tags_text"] = " ".join(record.tags)
		for field_name in _NULLABLE_FULL_TEXT_FIELDS:
			if payload.get(field_name) is None:
				payload[field_name] = ""
		return payload

	def _hydrate_record(self, payload: Mapping[str, Any]) -> MemoryRecord:
		data = dict(payload)
		data.pop("tags_text", None)
		for field_name in _NULLABLE_FULL_TEXT_FIELDS:
			if data.get(field_name) == "":
				data[field_name] = None
		data["scope"] = {
			"user_id": data.get("user_id"),
			"project_id": data.get("project_id"),
			"agent_id": data.get("agent_id"),
		}
		return MemoryRecord.model_validate(data)

	def _apply_properties(self, vertex: Any, properties: Mapping[str, Any]) -> None:
		for key, value in properties.items():
			vertex.set(key, value)
