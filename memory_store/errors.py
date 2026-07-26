"""Project exception hierarchy."""


class MemoryStoreError(Exception):
    """Base exception for the package."""


class ConfigError(MemoryStoreError):
    """Raised when configuration loading fails."""


class ValidationError(MemoryStoreError):
    """Raised when model validation fails."""


class MemoryNotFoundError(MemoryStoreError):
    """Raised when a memory record cannot be found."""


class SchemaMismatchError(MemoryStoreError):
    """Raised when the database schema version does not match."""


class EmbeddingDimensionMismatchError(MemoryStoreError):
    """Raised when embedding dimensions do not match the configured model."""


class PersistenceError(MemoryStoreError):
    """Raised when persistence operations fail."""


class PrivacyError(MemoryStoreError):
    """Raised when a privacy control operation fails."""


class LifecycleError(MemoryStoreError):
    """Raised when a lifecycle transition is invalid."""


class IngestionError(MemoryStoreError):
    """Raised when deterministic ingestion fails."""


class RetrievalError(MemoryStoreError):
    """Raised when retrieval execution fails."""
