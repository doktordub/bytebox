"""Deterministic ArcadeDB schema creation for ByteBox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import PersistenceError

MEMORY_RECORD_VERTEX = "MemoryRecord"
SCHEMA_VERSION_VERTEX = "SchemaVersion"
MIGRATION_RECORD_VERTEX = "MigrationRecord"
REDACTION_AUDIT_VERTEX = "RedactionAudit"
IMPORT_EXPORT_AUDIT_VERTEX = "ImportExportAudit"

EDGE_TYPES = (
	"SUPERSEDES",
	"CONTRADICTS",
	"DERIVED_FROM",
	"RELATED_TO",
	"SUPPORTS",
	"MENTIONS",
)

FULL_TEXT_INDEX_FIELDS = ("title", "summary", "text", "tags_text", "source_path")
METADATA_INDEX_FIELDS = (
	"memory_id",
	"memory_type",
	"project_id",
	"user_id",
	"agent_id",
	"status",
	"created_at",
	"stable_key",
	"source_hash",
	"chunk_id",
	"section_index",
	"section_chunk_index",
	"document_chunk_index",
)


@dataclass(frozen=True, slots=True)
class PropertySpec:
	name: str
	property_type: str
	of_type: str | None = None


MEMORY_RECORD_PROPERTIES = (
	PropertySpec("memory_id", "STRING"),
	PropertySpec("stable_key", "STRING"),
	PropertySpec("version", "INTEGER"),
	PropertySpec("schema_version", "INTEGER"),
	PropertySpec("memory_type", "STRING"),
	PropertySpec("status", "STRING"),
	PropertySpec("sensitivity", "STRING"),
	PropertySpec("user_id", "STRING"),
	PropertySpec("project_id", "STRING"),
	PropertySpec("agent_id", "STRING"),
	PropertySpec("title", "STRING"),
	PropertySpec("summary", "STRING"),
	PropertySpec("text", "STRING"),
	PropertySpec("tags", "LIST", "STRING"),
	PropertySpec("tags_text", "STRING"),
	PropertySpec("source_type", "STRING"),
	PropertySpec("source_path", "STRING"),
	PropertySpec("source_hash", "STRING"),
	PropertySpec("source_uri", "STRING"),
	PropertySpec("chunk_id", "STRING"),
	PropertySpec("heading_path", "LIST", "STRING"),
	PropertySpec("section_index", "INTEGER"),
	PropertySpec("section_chunk_index", "INTEGER"),
	PropertySpec("document_chunk_index", "INTEGER"),
	PropertySpec("chunk_index", "INTEGER"),
	PropertySpec("embedding", "ARRAY_OF_FLOATS"),
	PropertySpec("embedding_model", "STRING"),
	PropertySpec("embedding_model_version", "STRING"),
	PropertySpec("embedding_dim", "INTEGER"),
	PropertySpec("embedding_created_at", "DATETIME"),
	PropertySpec("confidence", "DOUBLE"),
	PropertySpec("importance", "DOUBLE"),
	PropertySpec("user_rating", "DOUBLE"),
	PropertySpec("valid_from", "DATETIME"),
	PropertySpec("valid_to", "DATETIME"),
	PropertySpec("expires_at", "DATETIME"),
	PropertySpec("allow_retrieval", "BOOLEAN"),
	PropertySpec("allow_llm_context", "BOOLEAN"),
	PropertySpec("retention_policy", "STRING"),
	PropertySpec("metadata", "MAP"),
	PropertySpec("created_at", "DATETIME"),
	PropertySpec("updated_at", "DATETIME"),
	PropertySpec("last_accessed_at", "DATETIME"),
	PropertySpec("superseded_by", "STRING"),
)

SCHEMA_VERSION_PROPERTIES = (
	PropertySpec("key", "STRING"),
	PropertySpec("version", "INTEGER"),
	PropertySpec("min_compatible_version", "INTEGER"),
	PropertySpec("updated_at", "DATETIME"),
)

MIGRATION_RECORD_PROPERTIES = (
	PropertySpec("version", "INTEGER"),
	PropertySpec("name", "STRING"),
	PropertySpec("applied_at", "DATETIME"),
)

AUDIT_PROPERTIES = (
	PropertySpec("operation", "STRING"),
	PropertySpec("scope_key", "STRING"),
	PropertySpec("memory_id", "STRING"),
	PropertySpec("details", "MAP"),
	PropertySpec("created_at", "DATETIME"),
)


def required_vertex_types() -> tuple[str, ...]:
	return (
		MEMORY_RECORD_VERTEX,
		SCHEMA_VERSION_VERTEX,
		MIGRATION_RECORD_VERTEX,
		REDACTION_AUDIT_VERTEX,
		IMPORT_EXPORT_AUDIT_VERTEX,
	)


def required_edge_types() -> tuple[str, ...]:
	return EDGE_TYPES


def required_index_aliases() -> tuple[str, ...]:
	aliases = [f"{MEMORY_RECORD_VERTEX}[{field}]" for field in FULL_TEXT_INDEX_FIELDS]
	aliases.extend(f"{MEMORY_RECORD_VERTEX}[{field}]" for field in METADATA_INDEX_FIELDS)
	aliases.append(f"{SCHEMA_VERSION_VERTEX}[key]")
	aliases.append(f"{MIGRATION_RECORD_VERTEX}[version]")
	return tuple(aliases)


def ensure_schema(
	database: Any,
	*,
	schema_version: int,
	embedding_dimensions: int,
) -> None:
	if embedding_dimensions < 1:
		raise PersistenceError("Embedding dimensions must be >= 1 to initialize the vector index.")

	schema = database.schema

	_ensure_vertex_type(schema, MEMORY_RECORD_VERTEX, MEMORY_RECORD_PROPERTIES)
	_ensure_vertex_type(schema, SCHEMA_VERSION_VERTEX, SCHEMA_VERSION_PROPERTIES)
	_ensure_vertex_type(schema, MIGRATION_RECORD_VERTEX, MIGRATION_RECORD_PROPERTIES)
	_ensure_vertex_type(schema, REDACTION_AUDIT_VERTEX, AUDIT_PROPERTIES)
	_ensure_vertex_type(schema, IMPORT_EXPORT_AUDIT_VERTEX, AUDIT_PROPERTIES)

	for edge_type in EDGE_TYPES:
		schema.get_or_create_edge_type(edge_type)

	for field_name in FULL_TEXT_INDEX_FIELDS:
		schema.get_or_create_index(
			MEMORY_RECORD_VERTEX,
			[field_name],
			unique=False,
			index_type="FULL_TEXT",
		)

	schema.get_or_create_index(MEMORY_RECORD_VERTEX, ["memory_id"], unique=True)
	for field_name in METADATA_INDEX_FIELDS:
		if field_name == "memory_id":
			continue
		schema.get_or_create_index(MEMORY_RECORD_VERTEX, [field_name], unique=False)

	schema.get_or_create_index(SCHEMA_VERSION_VERTEX, ["key"], unique=True)
	schema.get_or_create_index(MIGRATION_RECORD_VERTEX, ["version"], unique=True)

	if schema.get_vector_index(MEMORY_RECORD_VERTEX, "embedding") is None:
		database.create_vector_index(
			MEMORY_RECORD_VERTEX,
			"embedding",
			dimensions=embedding_dimensions,
			id_property="memory_id",
			distance_function="cosine",
			build_graph_now=False,
		)


def _ensure_vertex_type(schema: Any, type_name: str, properties: tuple[PropertySpec, ...]) -> None:
	schema.get_or_create_vertex_type(type_name)
	for prop in properties:
		schema.get_or_create_property(type_name, prop.name, prop.property_type, of_type=prop.of_type)
