# ADR 0002: Provider Boundaries

- Status: Accepted
- Date: 2026-07-26

## Context

The current system has a single FastEmbed implementation path for embeddings and reranking. Runtime construction happens inside the service layer, and the observed Phase 0 baseline shows that model initialization can trigger live downloads and environment-specific failures.

ByteBox needs stable provider seams before it adds offline FastEmbed management, Ollama, or llama.cpp adapters.

## Decision

ByteBox will introduce explicit provider boundaries for embeddings and reranking.

The provider contract must own:

1. capability identity (`embedding`, `reranking`);
2. model identity and version reporting;
3. startup validation and health;
4. safe error translation;
5. lifecycle ownership of heavyweight runtimes.

Application use cases will depend on provider interfaces, not on FastEmbed classes or request-time endpoint construction.

Provider endpoints, model paths, and transport settings will be startup configuration only. Request payloads must never supply arbitrary provider URLs.

## Consequences

- FastEmbed becomes one adapter rather than the product architecture.
- Reranker and embedding runtimes can be initialized once per application lifecycle instead of once per request.
- Offline model manifests and checksum policy can be enforced at the provider boundary.
- Future remote providers can be added without changing core use cases.