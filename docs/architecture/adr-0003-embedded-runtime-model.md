# ADR 0003: Embedded Runtime Model

- Status: Accepted
- Date: 2026-07-26

## Context

The current application opens ArcadeDB Embedded while `MemoryService` is constructed. The FastAPI lifespan only handles shutdown. Phase 0 testing on Windows also showed that the native ArcadeDB / JPype stack is the most unstable part of the current runtime.

ByteBox needs an explicit runtime model that treats embedded persistence as a managed resource with clear ownership and startup failure semantics.

## Decision

ByteBox will keep embedded ArcadeDB as a single-process runtime mode and formalize these rules:

1. exactly one database owner exists per process;
2. database open and schema validation happen during application startup, not at import time and not during plain object construction;
3. startup must fail before serving requests if database or provider requirements fail;
4. shutdown must close providers, clients, and the database in reverse dependency order;
5. embedded mode must reject multi-worker process models.

The synchronous Python facade can remain, but it must delegate to the same resource-owner abstraction used by the API lifecycle.

## Consequences

- The current eager-open pattern in `MemoryService` is temporary and must be removed.
- FastAPI lifespan becomes the authoritative startup and shutdown owner for the service process.
- Operational health can distinguish liveness from readiness because readiness will now correspond to fully initialized dependencies.
- Horizontal scaling remains out of scope until a non-embedded persistence mode exists.