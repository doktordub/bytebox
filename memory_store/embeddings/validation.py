"""Embedding validation helpers."""

from __future__ import annotations

from typing import Any

from ..arcade.schema import MEMORY_RECORD_VERTEX
from ..errors import EmbeddingDimensionMismatchError


def validate_embedding_dimensions(
    expected: int,
    actual: int,
    *,
    context: str = "embedding",
) -> None:
    if expected != actual:
        raise EmbeddingDimensionMismatchError(
            f"Expected {context} dimension {expected}, received {actual}."
        )


def read_active_index_dimension(
    database: Any,
    *,
    vertex_type: str = MEMORY_RECORD_VERTEX,
    property_name: str = "embedding",
) -> int:
    index = database.schema.get_vector_index(vertex_type, property_name)
    if index is None:
        raise EmbeddingDimensionMismatchError(
            f"Vector index is not initialized for {vertex_type}.{property_name}."
        )

    metadata = index.get_metadata()
    dimensions = metadata.get("dimensions")
    if not isinstance(dimensions, int) or dimensions < 1:
        raise EmbeddingDimensionMismatchError(
            f"Vector index {vertex_type}.{property_name} returned an invalid "
            f"dimension: {dimensions!r}."
        )
    return dimensions


def validate_active_index_dimension(
    database: Any,
    expected: int,
    *,
    vertex_type: str = MEMORY_RECORD_VERTEX,
    property_name: str = "embedding",
) -> int:
    actual = read_active_index_dimension(
        database,
        vertex_type=vertex_type,
        property_name=property_name,
    )
    validate_embedding_dimensions(expected, actual, context="active vector index")
    return actual
