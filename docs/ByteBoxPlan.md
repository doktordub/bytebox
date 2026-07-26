# ByteBox Production Refactor Plan

**Source project:** `doktordub/mem-store`  
**Target project name:** ByteBox  
**Target repository:** new repository; recommended repository slug `bytebox`  
**Document date:** 2026-07-18  
**Plan status:** Production refactor proposal based on static repository review

---

## 1. Executive Summary

`mem-store` has a useful local-first foundation: a Python API, FastAPI adapter, embedded ArcadeDB persistence, FastEmbed embeddings, hybrid retrieval, lifecycle/privacy operations, CLI support, and a meaningful test suite organized around implementation phases. The production refactor should preserve those behaviors while changing the system from a tightly coupled, single-provider application into a modular, observable, secure ByteBox service.

The recommended approach is **not** a global search-and-replace rename. ByteBox should be created in a new repository with a clean `src/bytebox` package, explicit application composition, narrowly scoped services, provider interfaces, a managed resource lifespan, hardened API boundaries, reproducible local model management, and enforceable release gates.

The most important current-state findings are:

1. `memory_store/service.py` is 1,666 lines and combines memory CRUD, retrieval, ingestion, lifecycle, privacy, embeddings, database ownership, health, statistics, and recovery logic.
2. The database is opened eagerly while `MemoryService` is constructed. The FastAPI lifespan currently performs shutdown cleanup but does not own startup initialization.
3. FastEmbed is the only embedding implementation. Its runtime is created from a Hugging Face model identifier without configurable local model paths, cache directories, offline enforcement, model manifests, or integrity validation.
4. The reranker runtime is instantiated on each rerank request, creating avoidable latency and resource churn.
5. Search retrieves all records in a scope and performs filtering, vector search, and full-text search in Python. That design will degrade as record counts grow.
6. The API uses one optional token for all routes, compares it directly, returns raw exception strings for several failures, and allows caller-supplied local filesystem and manifest paths for ingestion.
7. Logging configuration exists, but the reviewed code does not implement a structured logging pipeline, trace IDs, redaction, or a true `off` mode.
8. Health combines several concerns into one response and exposes the database path. It does not distinguish liveness, readiness, operational status, or privileged state.
9. The project configuration does not currently enforce test coverage or include production security scanning/release automation.

The plan is organized into eleven ordered phases. Each phase has deliverables and an exit gate so ByteBox can be refactored incrementally without allowing architecture, security, performance, or compatibility debt to accumulate.

---

## 2. Review Basis and Limitations

### 2.1 Reviewed material

The review covered the public repository structure, project metadata, example configuration, architecture documentation, test tree, and key implementation files, including:

- `pyproject.toml`
- `config.example.yaml`
- `memory_store/service.py`
- `memory_store/store.py`
- `memory_store/config.py`
- `memory_store/models.py`
- `memory_store/cli.py`
- `memory_store/api/main.py`
- `memory_store/api/routes.py`
- `memory_store/api/schemas.py`
- `memory_store/embeddings/fastembed_provider.py`
- `memory_store/retrieval/rerank.py`
- `memory_store/retrieval/vector.py`
- `memory_store/arcade/connection.py`
- `memory_store/arcade/queries.py`
- `memory_store/arcade/schema.py`
- lifecycle, privacy, and scoring modules

### 2.2 Limitation

This is a **static code and architecture review**. The repository test suite and benchmarks were not executed in the review environment, so the current passing-test count, current branch coverage, and runtime performance baseline are unknown. Phase 0 explicitly establishes those facts before behavior-changing refactoring begins.

### 2.3 External implementation references

The proposed design is aligned with current official documentation for FastAPI lifespan management, FastEmbed local/offline model support, Ollama embedding APIs, llama.cpp embedding/reranking server endpoints, Uvicorn TLS, HTTPX TLS, OpenTelemetry context propagation, OWASP logging/SSRF guidance, and coverage enforcement. References are listed in Appendix F.

---

## 3. Target Outcomes

ByteBox will be considered production-ready when it has all of the following characteristics:

- A new, consistently branded repository, distribution, Python package, CLI, API title, configuration namespace, documentation set, and release process.
- Feature-oriented modules with clear dependency boundaries and no general-purpose monolithic service.
- One embedded database owner per process, opened during application lifespan startup and closed safely during shutdown.
- Pluggable embedding and reranking providers.
- Reproducible FastEmbed operation with pre-provisioned local models and a strict no-download/offline mode.
- Ollama and llama.cpp providers reachable over configurable local-network HTTP or HTTPS endpoints.
- TLS disabled by default for the ByteBox API and remote model connections, with explicit configuration for server TLS, certificate verification, custom certificate authorities, and optional mutual TLS.
- Secure authentication/authorization boundaries, safe filesystem ingestion, sanitized errors, SSRF defenses, secret-safe configuration, and redacted logs.
- Structured logs with trace IDs and exact `debug`, `info`, `warn`, and `off` levels.
- Stable liveness, readiness, status, state, and metrics contracts.
- At least 85% branch-aware test coverage enforced in CI.
- Measured performance baselines and regression gates.
- Reproducible builds, dependency scanning, secret scanning, SBOM generation, release artifacts, migration tooling, and operational documentation.

---

## 4. Explicit Non-Goals for the Initial ByteBox Release

These should not silently enter the refactor scope:

- Multi-process access to one ArcadeDB Embedded database.
- Horizontal scaling of one embedded database across multiple ByteBox instances.
- A hosted multi-tenant control plane.
- Arbitrary model endpoint URLs supplied in individual API requests.
- Automatic execution of untrusted model code.
- Native Ollama cross-encoder reranking, because Ollama's documented API provides embeddings and generation/chat rather than a dedicated cross-encoder rerank endpoint.
- Breaking or destructive database migration without backup, dry-run, and rollback support.

ByteBox should validate `workers == 1` for embedded mode. A future external-database adapter can enable horizontal scaling without compromising the initial embedded architecture.

---

## 5. Current-State Findings and Required Refactors

### 5.1 Architecture and module ownership

| Finding | Production impact | Required change |
|---|---|---|
| `MemoryService` owns most application behavior in 1,666 lines | High change risk, difficult testing, circular responsibility, weak ownership | Split by use case: memory commands, retrieval, ingestion, lifecycle, privacy, administration, and model orchestration |
| `models.py`, `cli.py`, and `config.py` are also broad modules | Continues monolithic growth outside the service | Split domain models, API schemas, config sections, and CLI commands by capability |
| API routes directly close over a concrete store | Harder dependency injection and testing | Resolve application services from an application container/dependency provider |
| Persistence and provider construction occur inside service logic | Difficult startup validation and runtime replacement | Compose repositories/providers in bootstrap code and inject interfaces |

### 5.2 Resource lifecycle

Current construction follows this effective path:

```text
create_app()
  -> MemoryStore(...)
     -> MemoryService(...)
        -> _ensure_repository()
           -> open ArcadeDB and ensure schema
  -> create FastAPI lifespan that only closes the store
```

This means database startup happens before FastAPI enters lifespan. ByteBox should instead use:

```text
create_app(settings_source)
  -> create lightweight FastAPI app
  -> lifespan startup
       validate settings
       configure logging and tracing
       open database once
       run/validate migrations
       build repositories
       initialize shared HTTP clients
       initialize/warm selected model providers
       publish ready application container
  -> serve requests
  -> lifespan shutdown
       stop accepting work
       drain bounded in-flight work
       close provider clients/runtimes
       close database once
       close telemetry exporters
```

### 5.3 Model providers

Current FastEmbed construction only provides `model_name`. ByteBox needs:

- model cache directory;
- explicit local model path;
- local-files-only behavior;
- global offline mode;
- expected dimension and normalization policy;
- model manifest and checksum;
- runtime thread/provider settings;
- startup validation and optional warmup;
- stable model identity recorded with each embedding;
- a controlled re-embedding workflow when model identity or vector dimension changes.

The current reranker constructs `TextCrossEncoder(model)` for each rerank operation. ByteBox should construct and cache one provider runtime per configured model within the application container.

### 5.4 API and security

The following need to be addressed before exposing ByteBox beyond localhost:

- Raw exception text must not be returned for internal errors.
- Direct token comparison should be replaced by constant-time verification.
- Secrets must use secret-bearing configuration types and never appear in model dumps, error strings, health output, or logs.
- Read, write, ingest, export/import, destructive operations, and administration must not share one undifferentiated authorization level.
- Caller-supplied filesystem paths must be constrained to configured ingest roots after canonicalization and symlink resolution.
- Caller-supplied manifest output paths should be removed from the public API; ByteBox should generate manifests under a configured state directory.
- Request size, list size, text length, batch size, context-window size, and ingestion limits must be enforced at the API boundary.
- API documentation endpoints must be configurable and disabled or protected in production mode.
- Provider endpoint URLs must be static configuration, not request input, and protected against SSRF and unsafe redirects.

### 5.5 Logging and tracing

The reviewed code contains logging settings but no complete logging implementation. ByteBox needs one logging bootstrap point and one event vocabulary. Logs should contain metadata, not memory contents or secrets.

Required fields for structured events:

```text
timestamp
severity
event
service.name
service.version
trace_id
span_id
request_id
operation
component
outcome
duration_ms
safe_error_code
```

Data excluded by default:

- API tokens, bearer tokens, cookies, passwords, client secrets, model endpoint credentials;
- certificate/key contents and key passwords;
- database connection strings;
- raw memory text, raw queries, imported/exported payloads, embeddings, reranker document bodies;
- full filesystem paths unless explicitly enabled for local debugging;
- raw remote-provider response bodies on failure.

### 5.6 Health, state, and status

Replace the current single endpoint with stable contracts:

- `GET /health/live`: process is alive and event loop can respond.
- `GET /health/ready`: database, schema, required provider initialization, and required storage checks are ready.
- `GET /status`: sanitized build/runtime summary safe for authenticated operators.
- `GET /state`: privileged operational state, counters, circuit-breaker state, and safe recent error codes.
- `GET /metrics`: optional Prometheus/OpenMetrics endpoint, separately configurable and protected as needed.

The public response must not expose absolute database paths, credentials, certificate paths, model endpoint credentials, exception strings, or stored content.

### 5.7 Performance

The highest-priority retrieval issue is the current pattern of loading all records in a scope and then applying filtering/vector/full-text matching in Python. ByteBox should push bounded candidate selection into the database/index layer and hydrate only the candidate set needed for fusion, graph expansion, reranking, and final scoring.

Other performance work:

- reuse model runtimes;
- reuse pooled HTTP clients;
- batch embeddings and remote calls;
- use bounded concurrency and backpressure;
- isolate blocking embedded-database and ONNX work from the async event loop;
- collapse statistics into aggregate database queries;
- avoid repeated model identity discovery;
- make debug component scores opt-in in production;
- cap candidate sets before graph expansion and reranking.

---

## 6. Target ByteBox Architecture

### 6.1 Dependency rule

Dependencies flow inward:

```text
API / CLI / Workers
        |
        v
Application use cases and ports
        |
        v
Domain models and rules
        ^
        |
Infrastructure adapters: ArcadeDB, FastEmbed, Ollama, llama.cpp, HTTP, telemetry
```

Domain and application modules must not import FastAPI, HTTPX, FastEmbed, ArcadeDB, Uvicorn, or CLI libraries.

### 6.2 Target package structure

```text
bytebox/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE                         # after license/provenance review
├── SECURITY.md
├── CONTRIBUTING.md
├── config/
│   ├── bytebox.example.yaml
│   └── logging.example.yaml
├── docs/
│   ├── architecture/
│   ├── operations/
│   ├── security/
│   ├── migration/
│   └── providers/
├── src/bytebox/
│   ├── __init__.py
│   ├── version.py
│   ├── bootstrap/
│   │   ├── app_factory.py
│   │   ├── container.py
│   │   ├── lifespan.py
│   │   └── validation.py
│   ├── config/
│   │   ├── models.py
│   │   ├── loader.py
│   │   ├── environment.py
│   │   ├── secrets.py
│   │   └── validation.py
│   ├── domain/
│   │   ├── memory/
│   │   │   ├── entities.py
│   │   │   ├── value_objects.py
│   │   │   ├── lifecycle.py
│   │   │   └── privacy.py
│   │   ├── documents/
│   │   │   ├── entities.py
│   │   │   └── chunking.py
│   │   └── retrieval/
│   │       ├── candidates.py
│   │       ├── scoring.py
│   │       └── policies.py
│   ├── application/
│   │   ├── ports/
│   │   │   ├── repositories.py
│   │   │   ├── embeddings.py
│   │   │   ├── rerankers.py
│   │   │   ├── clock.py
│   │   │   └── unit_of_work.py
│   │   ├── memory/
│   │   │   ├── commands.py
│   │   │   ├── queries.py
│   │   │   └── service.py
│   │   ├── ingestion/
│   │   │   ├── service.py
│   │   │   ├── pipeline.py
│   │   │   ├── manifests.py
│   │   │   └── recovery.py
│   │   ├── retrieval/
│   │   │   ├── service.py
│   │   │   ├── pipeline.py
│   │   │   └── model_compatibility.py
│   │   ├── privacy/
│   │   │   └── service.py
│   │   └── administration/
│   │       ├── health.py
│   │       ├── status.py
│   │       └── state.py
│   ├── infrastructure/
│   │   ├── persistence/arcadedb/
│   │   │   ├── connection.py
│   │   │   ├── repository.py
│   │   │   ├── queries.py
│   │   │   ├── schema.py
│   │   │   └── migrations/
│   │   ├── models/
│   │   │   ├── registry.py
│   │   │   ├── identity.py
│   │   │   ├── fastembed/
│   │   │   │   ├── embedding.py
│   │   │   │   ├── reranker.py
│   │   │   │   ├── local_models.py
│   │   │   │   └── validation.py
│   │   │   ├── ollama/
│   │   │   │   ├── embedding.py
│   │   │   │   └── llm_reranker.py
│   │   │   └── llamacpp/
│   │   │       ├── embedding.py
│   │   │       └── reranker.py
│   │   ├── http/
│   │   │   ├── client_factory.py
│   │   │   ├── tls.py
│   │   │   ├── retries.py
│   │   │   ├── circuit_breaker.py
│   │   │   └── endpoint_policy.py
│   │   └── observability/
│   │       ├── logging.py
│   │       ├── redaction.py
│   │       ├── tracing.py
│   │       └── metrics.py
│   ├── api/
│   │   ├── app.py
│   │   ├── dependencies.py
│   │   ├── errors.py
│   │   ├── middleware/
│   │   │   ├── tracing.py
│   │   │   ├── request_limits.py
│   │   │   └── security_headers.py
│   │   ├── routes/
│   │   │   ├── health.py
│   │   │   ├── memories.py
│   │   │   ├── retrieval.py
│   │   │   ├── ingestion.py
│   │   │   ├── privacy.py
│   │   │   └── administration.py
│   │   └── schemas/
│   └── cli/
│       ├── main.py
│       └── commands/
│           ├── serve.py
│           ├── memory.py
│           ├── ingest.py
│           ├── models.py
│           ├── config.py
│           └── database.py
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── security/
│   ├── performance/
│   └── fixtures/
└── .github/workflows/
    ├── ci.yaml
    ├── security.yaml
    ├── release.yaml
    └── codeql.yaml
```

### 6.3 Module-size policy

- Prefer modules below 300 logical lines of code.
- A module over 400 logical lines requires an architecture-review exception.
- A class should have one primary reason to change.
- Public interfaces live in `application/ports`; infrastructure implementations live outside the application layer.
- API schemas are separate from domain entities.
- Configuration models are separate from loading, environment parsing, secret resolution, and validation.
- CLI commands call application services; they do not duplicate business logic.

This is a maintainability policy, not a mechanical line-splitting exercise. Cohesion and dependency direction take priority over arbitrary file counts.

---

## 7. Provider Architecture

### 7.1 Embedding provider contract

```python
class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> ModelIdentity: ...

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def health(self, deep: bool = False) -> ProviderHealth: ...
    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]: ...
    async def embed_query(self, text: str) -> Embedding: ...
```

Required provider guarantees:

- one output per input, in input order;
- finite numeric values;
- validated dimension;
- explicit normalization behavior;
- stable model identity;
- bounded request and batch sizes;
- safe errors that do not contain secrets or raw documents;
- health behavior with bounded timeouts.

### 7.2 Reranker provider contract

```python
class RerankerProvider(Protocol):
    @property
    def identity(self) -> ModelIdentity: ...

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def health(self, deep: bool = False) -> ProviderHealth: ...
    async def rerank(
        self,
        query: str,
        documents: Sequence[RerankDocument],
        top_n: int,
    ) -> list[RerankScore]: ...
```

### 7.3 Supported provider modes

| Capability | FastEmbed | Ollama | llama.cpp |
|---|---:|---:|---:|
| Dense embeddings | Yes | Yes, `/api/embed` | Yes, OpenAI-compatible embeddings endpoint |
| Native cross-encoder reranking | Yes | No documented dedicated endpoint | Yes, server rerank endpoint/aliases |
| LLM-based listwise reranking | Not needed | Optional adapter using structured generation | Optional but native reranker preferred |
| Local model files | Yes | Managed by Ollama host | Managed by llama.cpp host |
| HTTP/HTTPS | In-process | Yes | Yes |

For Ollama, ByteBox may provide an **optional LLM listwise reranker** using generation/chat with temperature `0`, a strict JSON schema, document IDs rather than full result objects, response validation, token limits, and deterministic fallback. It must be clearly labeled as different from a cross-encoder reranker and disabled by default.

### 7.4 Model identity and compatibility

Store an immutable identity with every vector:

```text
provider
model_name
model_revision_or_digest
runtime_version
vector_dimension
normalization
pooling/config fingerprint
model_artifact_checksum, when local
```

ByteBox must reject search that combines incompatible vector spaces unless an explicitly configured migration policy applies. A model change should produce one of these controlled outcomes:

- `error`: refuse startup/search and explain required migration;
- `quarantine`: keep incompatible records but exclude them;
- `reembed`: enqueue or execute a resumable re-embedding job after backup.

Never silently mix dimensions or embeddings generated by materially different models.

---

## 8. Configuration and Environment Design

### 8.1 Precedence

Recommended precedence:

```text
built-in defaults
  < YAML configuration
  < environment variables
  < secret-file references
  < explicit process/CLI overrides
```

Secrets should not be accepted as command-line values because process listings and shell history can expose them.

### 8.2 Proposed configuration example

```yaml
application:
  environment: development           # development | test | production
  data_dir: ./data
  state_dir: ./state

models:
  cache_dir: ./models
  offline: true
  verify_checksums: true
  warmup_on_start: false

embeddings:
  provider: fastembed                 # fastembed | ollama | llamacpp
  model: BAAI/bge-small-en-v1.5
  expected_dimension: 384
  normalize: true
  batch_size: 64
  mismatch_policy: error              # error | quarantine | reembed

  fastembed:
    model_path: ./models/bge-small-en-v1.5
    cache_dir: ./models/cache
    local_files_only: true
    hf_hub_offline: true
    threads: null
    execution_providers: []

  endpoint:
    base_url: http://192.168.1.20:11434
    api_path: /api/embed
    api_key_env: BYTEBOX_EMBEDDINGS_API_KEY
    connect_timeout_seconds: 5
    read_timeout_seconds: 30
    write_timeout_seconds: 30
    pool_timeout_seconds: 5
    max_connections: 20
    max_keepalive_connections: 10
    max_retries: 2
    follow_redirects: false
    allowed_hosts:
      - 192.168.1.20
    allowed_cidrs:
      - 192.168.1.0/24
    tls:
      enabled: false
      verify: true
      ca_file: null
      client_cert_file: null
      client_key_file: null
      client_key_password_env: null
      server_name: null

reranking:
  enabled: true
  provider: fastembed                 # none | fastembed | ollama | llamacpp
  model: Xenova/ms-marco-MiniLM-L-6-v2
  top_n: 60
  batch_size: 32

  fastembed:
    model_path: ./models/ms-marco-MiniLM-L-6-v2
    cache_dir: ./models/cache
    local_files_only: true

  endpoint:
    base_url: http://192.168.1.21:8080
    api_path: /v1/rerank
    api_key_env: BYTEBOX_RERANKING_API_KEY
    connect_timeout_seconds: 5
    read_timeout_seconds: 30
    max_retries: 2
    follow_redirects: false
    allowed_hosts:
      - 192.168.1.21
    allowed_cidrs:
      - 192.168.1.0/24
    tls:
      enabled: false
      verify: true
      ca_file: null
      client_cert_file: null
      client_key_file: null

api:
  enabled: true
  host: 127.0.0.1
  port: 8080
  workers: 1
  auth:
    enabled: true
    token_env: BYTEBOX_API_TOKEN
  docs_enabled: false
  max_request_body_bytes: 2097152
  tls:
    enabled: false
    cert_file: null
    key_file: null
    key_password_env: null
    client_ca_file: null
    require_client_certificate: false

security:
  ingest_roots:
    - ./documents
  allow_symlinks: false
  export_enabled: true
  import_enabled: true
  hard_delete_enabled: false
  trusted_hosts:
    - localhost
    - 127.0.0.1

logging:
  level: info                         # debug | info | warn | off
  format: json                        # json | console
  destination: stdout
  include_trace_id: true
  access_log: true
  log_content: false

telemetry:
  tracing_enabled: true
  metrics_enabled: true
  exporter: none                      # none | otlp
  otlp_endpoint: null
```

### 8.3 Required ByteBox environment variables

Use `BYTEBOX_` with `__` between nested sections.

| Variable | Purpose |
|---|---|
| `BYTEBOX_APPLICATION__ENVIRONMENT` | Runtime profile |
| `BYTEBOX_APPLICATION__DATA_DIR` | Data root |
| `BYTEBOX_APPLICATION__STATE_DIR` | State/manifests root |
| `BYTEBOX_MODELS__CACHE_DIR` | Shared local model cache |
| `BYTEBOX_MODELS__OFFLINE` | Disable network model acquisition |
| `BYTEBOX_MODELS__VERIFY_CHECKSUMS` | Require model integrity checks |
| `BYTEBOX_EMBEDDINGS__PROVIDER` | `fastembed`, `ollama`, or `llamacpp` |
| `BYTEBOX_EMBEDDINGS__MODEL` | Embedding model name |
| `BYTEBOX_EMBEDDINGS__EXPECTED_DIMENSION` | Required vector dimension |
| `BYTEBOX_EMBEDDINGS__FASTEMBED__MODEL_PATH` | Explicit local FastEmbed model path |
| `BYTEBOX_EMBEDDINGS__FASTEMBED__CACHE_DIR` | FastEmbed cache directory |
| `BYTEBOX_EMBEDDINGS__FASTEMBED__LOCAL_FILES_ONLY` | Forbid remote model resolution |
| `BYTEBOX_EMBEDDINGS__FASTEMBED__HF_HUB_OFFLINE` | Enable Hugging Face offline behavior |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__BASE_URL` | Ollama/llama.cpp base URL |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__API_KEY_ENV` | Name of secret-bearing env variable |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__TLS__ENABLED` | Enable outbound TLS mode |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__TLS__VERIFY` | Verify remote certificate |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__TLS__CA_FILE` | Custom CA bundle |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__TLS__CLIENT_CERT_FILE` | mTLS client certificate |
| `BYTEBOX_EMBEDDINGS__ENDPOINT__TLS__CLIENT_KEY_FILE` | mTLS client key |
| `BYTEBOX_RERANKING__PROVIDER` | Reranking provider |
| `BYTEBOX_RERANKING__FASTEMBED__MODEL_PATH` | Local FastEmbed reranker path |
| `BYTEBOX_RERANKING__ENDPOINT__BASE_URL` | Remote reranker base URL |
| `BYTEBOX_RERANKING__ENDPOINT__TLS__ENABLED` | Enable reranker TLS mode |
| `BYTEBOX_RERANKING__ENDPOINT__TLS__VERIFY` | Verify reranker certificate |
| `BYTEBOX_RERANKING__ENDPOINT__TLS__CA_FILE` | Reranker custom CA bundle |
| `BYTEBOX_API__HOST` / `BYTEBOX_API__PORT` | API bind address and port |
| `BYTEBOX_API__AUTH__TOKEN_ENV` | Name of API token env variable |
| `BYTEBOX_API_TOKEN` | Default API token secret value |
| `BYTEBOX_API__TLS__ENABLED` | Enable inbound API TLS |
| `BYTEBOX_API__TLS__CERT_FILE` | Server certificate |
| `BYTEBOX_API__TLS__KEY_FILE` | Server private key |
| `BYTEBOX_API__TLS__CLIENT_CA_FILE` | Client CA for mTLS |
| `BYTEBOX_API__TLS__REQUIRE_CLIENT_CERTIFICATE` | Require mTLS client certificate |
| `BYTEBOX_LOGGING__LEVEL` | `debug`, `info`, `warn`, or `off` |
| `BYTEBOX_LOGGING__FORMAT` | `json` or `console` |
| `BYTEBOX_TELEMETRY__TRACING_ENABLED` | Enable trace instrumentation |
| `BYTEBOX_TELEMETRY__METRICS_ENABLED` | Enable metrics |

### 8.4 Configuration validation rules

Startup must fail safely when:

- API TLS is enabled without readable certificate and key files.
- Outbound TLS is enabled while an `http://` base URL is configured.
- An `https://` endpoint has certificate verification disabled in production unless an explicit insecure override is allowed by policy.
- A client certificate is configured without its private key.
- Offline FastEmbed is enabled but the configured model is not locally available.
- A local model manifest/checksum is missing when checksum verification is required.
- The configured embedding dimension differs from the active database index.
- Embedded mode is configured with more than one server worker.
- Ingest roots are empty while filesystem ingestion endpoints are enabled.
- Provider hosts are outside the configured allowlist/CIDRs.
- a secret value appears directly in a field that requires an environment/file reference under production mode.

---

## 9. TLS Design

### 9.1 ByteBox API TLS

Default:

```yaml
api:
  tls:
    enabled: false
```

When enabled:

- construct a server SSL context from certificate/key configuration;
- support encrypted private keys through a secret reference;
- support a custom client CA and optional required client certificates;
- reject obsolete protocol versions through Python SSL-context defaults/policy;
- validate key/certificate readability and correspondence during startup;
- avoid logging certificate contents, private-key paths in public status, or key passwords;
- expose only `tls.enabled`, `tls.client_auth`, and safe certificate expiry metadata to privileged status.

ByteBox may also run behind a trusted TLS-terminating reverse proxy. Direct TLS and proxy-terminated TLS must be separately documented to avoid trusting spoofed forwarding headers.

### 9.2 Ollama and llama.cpp outbound TLS

For every remote model provider:

- TLS remains off by default through an `http://` URL and `tls.enabled: false`.
- Enabling TLS requires `https://` and certificate verification defaults to `true`.
- custom CAs are supported for private-network PKI;
- optional client certificate/key supports mTLS;
- hostname verification remains enabled;
- redirects are disabled unless explicitly approved, and every redirect target must be revalidated;
- proxy environment variables are ignored by default (`trust_env: false`) to prevent unexpected routing, with an explicit proxy configuration if needed;
- shared `httpx.AsyncClient` instances are created at startup and closed at shutdown;
- connect/read/write/pool timeouts and connection limits are mandatory;
- retries apply only to safe/transient failures and use bounded exponential backoff with jitter.

### 9.3 TLS test matrix

- HTTP succeeds when TLS is disabled.
- Trusted HTTPS succeeds with system CA.
- Private-CA HTTPS succeeds only with configured CA.
- Unknown CA fails closed.
- Hostname mismatch fails closed.
- Expired certificate fails closed.
- mTLS succeeds with an approved client certificate.
- mTLS fails without or with an untrusted client certificate.
- Secret fields and certificate material never appear in logs/status/errors.

---

## 10. Phased Refactor Plan

## Phase 0 — Baseline, Provenance, and Production Definition

### Objective

Create a verified baseline before changing behavior and remove legal, compatibility, security, and performance uncertainty.

### Work

1. Inventory public APIs, CLI commands, configuration keys, environment variables, database schema/version, stored model identity fields, REST routes, and documented workflows.
2. Execute all tests on a pinned clean environment and record failures/flakiness.
3. Measure statement and branch coverage by package.
4. Capture representative performance baselines for CRUD, search, document ingestion, startup, shutdown, and model initialization.
5. Build a threat model covering:
   - local filesystem ingestion;
   - API trust boundaries;
   - embedded database files;
   - model files and supply chain;
   - Ollama/llama.cpp network connections;
   - import/export and destructive operations;
   - logs and telemetry.
6. Verify source license, ownership, attribution, model licenses, and right to copy code into a new repository. The reviewed repository root did not visibly establish this through a root license file, so this is a mandatory gate.
7. Define supported platforms and minimum versions based on actual ArcadeDB/FastEmbed compatibility.
8. Establish production SLOs and performance targets from measured baselines rather than invented values.
9. Create architecture decision records for the new repository, clean-break versus compatibility behavior, provider contracts, and embedded single-process constraints.

### Deliverables

- `docs/baseline/current-system-inventory.md`
- `docs/baseline/test-and-coverage-report.md`
- `docs/baseline/performance-report.md`
- `docs/security/threat-model.md`
- `docs/legal/provenance-and-license-review.md`
- `docs/architecture/adr-0001-bytebox-repository-and-package.md`
- `docs/architecture/adr-0002-provider-boundaries.md`
- `docs/architecture/adr-0003-embedded-runtime-model.md`
- machine-readable API/config/database compatibility inventory

### Exit criteria

- Current tests and coverage are measured and reproducible.
- Known current defects are separated from refactor regressions.
- License/provenance permits the new repository strategy.
- Benchmark datasets contain no confidential data.
- Production success criteria and SLOs are approved.

---

## Phase 1 — New Repository and ByteBox Rebrand Foundation

### Objective

Create the clean ByteBox project foundation without changing core behavior.

### Work

1. Create the new repository and protect the default branch.
2. Adopt `src/bytebox` package layout.
3. Rename:
   - project/repository to ByteBox;
   - Python package to `bytebox`;
   - CLI to `bytebox`;
   - environment prefix to `BYTEBOX_`;
   - config example to `bytebox.example.yaml`;
   - API title, user agent, telemetry service name, docs, error codes, and artifact names.
4. Select the distribution name only after checking registry availability. If `bytebox` is unavailable or ambiguous, use a distribution such as `bytebox-memory` while keeping `import bytebox` and the `bytebox` CLI.
5. Do not copy generated `dist/` artifacts, test output captures, caches, secrets, local databases, model files, or editor state.
6. Add `README.md`, `LICENSE`, `NOTICE`/attribution as required, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, release policy, and support matrix.
7. Establish dependency locking and reproducible developer commands.
8. Add pre-commit checks for formatting, linting, typing, secrets, and file hygiene.
9. Decide compatibility strategy:
   - preferred: ByteBox is a new major product with explicit migration tooling;
   - optional: a small, separately tested `memory_store` import shim for one transition release, not indefinite duplicated code.

### Deliverables

- New ByteBox repository
- clean package/CLI skeleton
- branding and naming matrix
- initial CI skeleton
- migration compatibility policy
- contributor/security/release documentation

### Exit criteria

- No runtime `memory_store` branding remains except documented migration compatibility code.
- Package, CLI, tests, and documentation build under the new name.
- Repository contains no generated distributions, model binaries, database files, test-output dumps, or secrets.
- License and attribution are present.
- Protected-branch rules require CI and review.

---

## Phase 2 — Modular Core Decomposition

### Objective

Split monolithic modules while preserving behavior and public contracts.

### Work

1. Introduce domain entities/value objects independent of Pydantic and infrastructure where practical.
2. Define application ports for repositories, unit of work, embeddings, reranking, clock, IDs, and telemetry.
3. Split `MemoryService` into focused services:
   - `MemoryCommandService`: add/update/upsert;
   - `MemoryQueryService`: get/list;
   - `RetrievalService`: search pipeline;
   - `DocumentIngestionService`: document/folder ingestion;
   - `LifecycleService`: promote/supersede/contradict/expire/feedback;
   - `PrivacyService`: forget/delete/disable/export/import/redact;
   - `AdministrationService`: health/status/state/stats.
4. Move chunking, manifest, recovery, and progress behavior into ingestion collaborators.
5. Split the broad domain model module by memory, retrieval, ingestion, privacy, and administration.
6. Split API schemas from domain objects.
7. Split CLI commands and make them call application use cases.
8. Preserve behavior through characterization tests before moving each capability.
9. Enforce dependency rules with an architecture test such as Import Linter.
10. Adopt the module-size policy and document exceptions.

### Deliverables

- target package structure implemented
- application ports and provider-independent services
- characterization tests for existing behavior
- architecture dependency tests
- public compatibility facade, if selected
- deletion of the monolithic service after migration

### Exit criteria

- No service combines CRUD, retrieval, ingestion, lifecycle, privacy, and administration.
- Domain/application modules do not import FastAPI, ArcadeDB, HTTPX, FastEmbed, or Uvicorn.
- Existing functional tests pass against the new composition.
- No new module exceeds the line policy without a reviewed exception.
- Coverage does not fall below the Phase 0 baseline.

---

## Phase 3 — Managed Lifespan and Persistence Hardening

### Objective

Make FastAPI lifespan the sole owner of database and shared runtime resources.

### Work

1. Create a lightweight application factory that does not open the database.
2. Create an `ApplicationContainer` containing settings, database handle, repositories, services, model providers, shared HTTP clients, and telemetry resources.
3. In lifespan startup:
   - load and validate configuration;
   - initialize logging/tracing early without exposing secrets;
   - acquire the embedded database lock;
   - open/create the database once;
   - validate or run migrations;
   - validate active vector index dimensions;
   - build repositories and application services;
   - initialize model providers and optional warmup;
   - mark readiness only after required checks succeed.
4. In shutdown:
   - mark not ready;
   - stop accepting new long-running work;
   - drain bounded in-flight operations;
   - close providers/HTTP clients;
   - close database once;
   - flush/close telemetry;
   - make repeated `close()` calls safe.
5. Preserve a synchronous context-managed Python facade for library callers, backed by the same resource-owner abstraction.
6. Reject `workers > 1` in embedded mode.
7. Make migrations transactional/idempotent where ArcadeDB permits, with backup, dry-run, version checks, and safe failure.
8. Add startup/shutdown failure-injection tests.

### Deliverables

- `bootstrap/lifespan.py`
- `bootstrap/container.py`
- database resource manager
- migration runner and backup/restore commands
- readiness state machine
- lifecycle integration tests
- one-worker runtime validation

### Exit criteria

- Database is not opened during module import or app construction.
- Exactly one database handle is opened per application process.
- Startup fails before accepting requests if schema/provider requirements fail.
- Graceful shutdown closes every initialized resource in reverse order.
- Startup, partial-startup failure, cancellation, and repeated shutdown tests pass.
- Existing databases can be opened only after a successful compatibility/dry-run check and backup policy.

---

## Phase 4 — Provider Framework and Offline FastEmbed Models

### Objective

Make local, reproducible FastEmbed operation a first-class capability and establish provider contracts for all model backends.

### Work

1. Implement embedding/reranker protocols, registries, model identities, provider health, and safe provider errors.
2. Pin and test a FastEmbed version that supports local model path, cache directory, local-files-only operation, offline behavior, and custom model registration. Current FastEmbed release notes document improvements in these areas; ByteBox should use a tested compatible version rather than an unconstrained dependency.
3. Add FastEmbed settings:
   - `model_path`;
   - `cache_dir`;
   - `local_files_only`;
   - `hf_hub_offline`;
   - `threads`;
   - execution providers;
   - expected dimension;
   - model revision/digest;
   - manifest/checksum requirements.
4. Construct `TextEmbedding` and `TextCrossEncoder` once per provider lifecycle, not per request.
5. Add `bytebox models` commands:
   - `list`;
   - `inspect`;
   - `verify`;
   - `install --source <local-directory-or-approved-artifact>`;
   - `export-manifest`;
   - `doctor`.
6. Store a model manifest next to each local model:

```yaml
schema_version: 1
provider: fastembed
capability: embedding
model_name: BAAI/bge-small-en-v1.5
revision: <pinned revision>
artifact_format: onnx
vector_dimension: 384
normalization: true
files:
  - path: model.onnx
    sha256: <digest>
license: <identifier-or-reference>
```

7. In strict offline mode:
   - set/enforce Hugging Face offline behavior before provider initialization;
   - reject missing local artifacts;
   - prohibit runtime model download;
   - test with outbound network blocked.
8. Replace private FastEmbed runtime introspection with a supported identity source or ByteBox manifest.
9. Introduce re-embedding compatibility checks and a resumable migration job.

### Deliverables

- provider interfaces and registry
- local FastEmbed embedding adapter
- local FastEmbed reranker adapter
- model manifest/checksum subsystem
- offline model CLI and documentation
- offline/network-denied integration tests
- model compatibility/re-embedding workflow

### Exit criteria

- ByteBox starts and performs embedding/reranking with no internet access when local models are installed.
- Missing or invalid model files fail startup with a safe error code and no attempted download.
- Model artifacts are checksum-verified when required.
- Embedding and reranker runtimes are reused.
- Model identity is persisted and compatibility is enforced.
- Tests verify zero secret/content leakage from model errors.

---

## Phase 5 — Ollama and llama.cpp HTTP Providers with Outbound TLS

### Objective

Support custom embedding and reranking models hosted on a local network over controlled HTTP or HTTPS connections.

### Work

1. Add a shared asynchronous HTTP client factory with:
   - connection pooling;
   - independent connect/read/write/pool timeouts;
   - max connections/keepalive limits;
   - `trust_env: false` by default;
   - disabled redirects by default;
   - bounded retry policy;
   - circuit breaker;
   - concurrency semaphore;
   - trace-context propagation;
   - redacted request/response diagnostics.
2. Implement endpoint policy:
   - URLs come only from startup configuration;
   - allowed schemes are `http` and `https`;
   - host/CIDR allowlists support approved private-network addresses;
   - block link-local, multicast, unspecified, and cloud metadata destinations unless explicitly and narrowly allowed;
   - validate resolved IPs and protect against DNS rebinding;
   - revalidate any explicitly allowed redirect target.
3. Implement Ollama embeddings through the documented `/api/embed` contract, including batch input, dimension validation, normalized-vector metadata, model identity, and safe handling of timing metadata.
4. Implement llama.cpp embeddings through its documented server endpoint and configure the server for embedding mode.
5. Implement llama.cpp native reranking through the documented rerank endpoint/aliases.
6. Optionally implement Ollama LLM listwise reranking with a distinct provider name such as `ollama_llm`, strict schema validation, deterministic settings, token/document limits, and fallback to fused retrieval scores.
7. Add provider capability discovery/validation at startup without treating an untrusted response as configuration authority.
8. Add HTTP and HTTPS/mTLS transport profiles described in Section 9.
9. Add mock-provider contract tests and opt-in real-provider integration tests.

### Deliverables

- shared HTTP/TLS transport layer
- endpoint allowlist and SSRF controls
- Ollama embedding adapter
- llama.cpp embedding adapter
- llama.cpp reranker adapter
- optional Ollama LLM reranker adapter
- provider contract test suite
- local-network deployment examples for HTTP, private CA HTTPS, and mTLS

### Exit criteria

- Provider contract tests pass for all adapters.
- HTTP works with TLS disabled by default.
- HTTPS verifies certificates by default.
- Private CA and mTLS scenarios pass.
- Unknown CA, hostname mismatch, disallowed host/IP, redirects, timeout, malformed output, wrong dimension, and partial response fail safely.
- Shared clients are opened once during lifespan and closed at shutdown.
- No endpoint credential, body content, embedding, or raw provider error appears in logs or API responses.

---

## Phase 6 — API Security, Authorization, and Inbound TLS

### Objective

Harden ByteBox's externally reachable surface and separate privileges by operation risk.

### Work

1. Replace raw exception responses with a stable envelope:

```json
{
  "error": {
    "code": "BYTEBOX_RETRIEVAL_FAILED",
    "message": "The retrieval operation could not be completed.",
    "trace_id": "..."
  }
}
```

Detailed exceptions remain only in protected internal telemetry after redaction.

2. Use constant-time comparison for static tokens, secret-bearing config types, and key rotation support.
3. Define authorization scopes/roles:
   - `memory:read`;
   - `memory:write`;
   - `memory:ingest`;
   - `memory:export`;
   - `memory:import`;
   - `memory:delete`;
   - `admin:read`;
   - `admin:operate`.
4. Keep a simple local token mode, but design the auth port so JWT/OIDC or reverse-proxy identity can be added without changing use cases.
5. Restrict filesystem ingestion:
   - only paths under configured roots;
   - canonicalize before authorization;
   - reject traversal, disallowed symlinks, devices, FIFOs, sockets, and unexpected file types;
   - enforce file count/size/section/chunk limits;
   - generate manifests only under ByteBox state storage.
6. Separate destructive endpoints and require explicit confirmation/idempotency keys where appropriate.
7. Add request body limits, validation limits, timeouts, and bounded work queues.
8. Configure trusted hosts, CORS off by default, security response headers, and protected/disabled OpenAPI docs in production.
9. Implement API TLS and optional mTLS.
10. Ensure reverse-proxy mode trusts forwarding headers only from configured proxy addresses.
11. Run a secure-code review focused on injection, path handling, deserialization/import, authorization, secrets, cryptography configuration, and denial-of-service controls.

### Deliverables

- sanitized API error system
- authentication/authorization interfaces and local-token implementation
- operation scopes
- secure path resolver and ingest policy
- inbound TLS/mTLS configuration
- request/security middleware
- security tests and abuse-case tests
- API security documentation

### Exit criteria

- Raw internal exception strings are never returned by production API handlers.
- Every non-public route has an explicit authorization requirement.
- Filesystem ingestion cannot escape configured roots.
- Hard delete/import/export/admin behavior is independently authorized and configurable.
- TLS/mTLS test matrix passes.
- Security tests cover path traversal, symlinks, oversized payloads, malformed imports, token timing-safe verification, authorization boundaries, and error sanitization.

---

## Phase 7 — Structured Logging, Trace IDs, Health, Status, and State

### Objective

Make ByteBox operable and diagnosable without leaking secrets or content.

### Work

1. Implement one logging bootstrap before other resources initialize.
2. Support exact configured levels:
   - `debug`: detailed safe operational metadata;
   - `info`: normal lifecycle and outcome events;
   - `warn`: degraded behavior and recoverable operational risk;
   - `off`: disable ByteBox application logs, Uvicorn access logs, and configured handlers so no routine logs are emitted.
3. Normalize Python `WARNING` internally to the external `warn` setting.
4. Implement a central redaction processor with key-name denylist, header denylist, value-pattern detection, maximum field lengths, CR/LF neutralization, and safe exception rendering.
5. Create/accept W3C trace context, generate a trace ID when absent, store it in a `contextvars` context, return it as `X-Trace-ID`, and propagate it to remote providers.
6. Optionally instrument OpenTelemetry traces/metrics without making an exporter required.
7. Define event names and schemas; do not use ad hoc prose as the primary machine-readable signal.
8. Add liveness/readiness/status/state endpoints:

| Endpoint | Auth | Deep dependency calls | Intended use |
|---|---|---:|---|
| `/health/live` | configurable anonymous | No | Process restart decision |
| `/health/ready` | configurable | Bounded/configurable | Traffic admission |
| `/status` | operator read | No by default | Build/config/runtime summary |
| `/state` | admin | Optional | Operational diagnosis |
| `/metrics` | configurable | No | Monitoring scrape |

9. Add safe checks for:
   - database open/lock/schema;
   - storage writeability and free-space thresholds;
   - embedding/reranker initialization;
   - remote provider circuit state;
   - pending migrations/re-embedding jobs;
   - process uptime/version/build commit;
   - queue/concurrency saturation.
10. Make health response schemas stable and versioned; readiness returns HTTP 503 when required dependencies are unavailable.
11. Add a test harness that injects representative secrets into config, headers, paths, exceptions, and provider responses and proves they never appear in logs.

### Deliverables

- structured JSON/console logging
- trace middleware and provider propagation
- redaction library and secret-leak tests
- event vocabulary
- liveness/readiness/status/state APIs
- optional metrics and OpenTelemetry integration
- operations dashboard/runbook field definitions

### Exit criteria

- Every request has a trace ID returned to the caller.
- Cross-component and outbound-provider events share the trace context.
- `off` emits no ByteBox or access logs in automated tests.
- No test secret appears in logs, status/state, health, or API errors.
- Health endpoints are fast, bounded, stable, and do not expose absolute paths or content.
- Readiness accurately follows application lifecycle and dependency state.

---

## Phase 8 — Retrieval and Runtime Performance Optimization

### Objective

Optimize measured bottlenecks while preserving retrieval quality and correctness.

### Work

1. Replace full-scope materialization with repository-level bounded candidate queries:
   - apply hard filters in database queries;
   - use ArcadeDB vector index for top-N candidates;
   - use database full-text index for top-N candidates;
   - fetch only fields required for fusion;
   - hydrate complete records after candidate bounding.
2. Confirm ArcadeDB query/index behavior with explain plans and realistic data volumes.
3. Keep reciprocal-rank fusion, deduplication, graph expansion, and scoring application-level, but strictly bound each stage.
4. Cache local provider runtimes and stable model metadata.
5. Reuse HTTP connections and batch remote requests according to provider limits.
6. Execute blocking ArcadeDB/FastEmbed work in a controlled thread pool from async routes; avoid blocking the event loop.
7. Add backpressure for ingestion and provider calls rather than creating unbounded tasks.
8. Replace repeated statistics queries with grouped aggregate queries.
9. Make debug details/component scores disabled by default in production to reduce serialization and content exposure.
10. Add benchmark suites for:
    - cold/warm startup;
    - add/upsert/get;
    - vector, full-text, hybrid, graph-expanded, and reranked search;
    - local and remote embedding batches;
    - ingestion throughput and peak memory;
    - readiness checks;
    - graceful shutdown under load.
11. Establish CI regression thresholds using Phase 0 baselines. A recommended default is to fail on a statistically meaningful regression above the approved tolerance, rather than asserting absolute latency across heterogeneous runners.
12. Protect retrieval quality with fixed evaluation datasets and ranking metrics such as Recall@K, MRR, and nDCG.

### Deliverables

- indexed/bounded retrieval repository methods
- async blocking-work boundary
- provider pooling/caching/batching
- aggregate statistics queries
- benchmark and retrieval-quality suites
- performance profiles and tuning guide

### Exit criteria

- Search does not enumerate all scoped records in application memory.
- Query plans demonstrate index use for candidate retrieval.
- Reranker/model runtime creation is absent from per-request paths.
- Event-loop responsiveness remains within the approved SLO under concurrent blocking workloads.
- CI enforces approved performance and retrieval-quality regression gates.
- Peak memory and candidate-set sizes remain bounded under configured maximums.

---

## Phase 9 — Testing, 85%+ Coverage, and Continuous Security

### Objective

Make quality and security measurable release gates rather than review-time intentions.

### Work

1. Add branch-aware coverage configuration:

```toml
[tool.coverage.run]
branch = true
source = ["bytebox"]

[tool.coverage.report]
fail_under = 85
show_missing = true
skip_covered = false
```

2. Enforce `85%` or greater overall branch-aware coverage in CI. Set higher package gates for security-sensitive modules where practical, such as configuration/secrets, auth, TLS, endpoint policy, redaction, and migrations.
3. Organize tests:
   - unit tests for domain/application behavior;
   - contract tests for every provider implementation;
   - integration tests for ArcadeDB and API lifespan;
   - end-to-end CLI/API tests;
   - security and abuse-case tests;
   - TLS tests with ephemeral test CA/certificates;
   - offline model tests with network denied;
   - failure-injection and concurrency tests;
   - benchmark and retrieval-evaluation tests.
4. Add property-based tests for configuration merging, path canonicalization, filter construction, import validation, lifecycle transitions, and redaction.
5. Add mutation testing on critical authorization/redaction/path/TLS logic as a scheduled gate.
6. Run type checking in strict mode incrementally and remove broad missing-import suppression through typed adapters/stubs.
7. Add CI checks:
   - formatting/linting;
   - strict typing;
   - unit/integration/contract tests;
   - coverage gate;
   - dependency vulnerability audit;
   - static security analysis;
   - secret scanning;
   - CodeQL;
   - container/image scan;
   - license policy;
   - SBOM generation;
   - reproducible package build.
8. Pin dependencies with hashes or a lockfile and use automated update pull requests.
9. Produce signed release artifacts/images where the release environment supports signing and provenance attestations.

### Deliverables

- complete test taxonomy and fixtures
- coverage configuration and CI gate
- provider/TLS/offline/security test suites
- security workflows and dependency policy
- SBOM and build provenance
- release candidate quality report

### Exit criteria

- Main branch coverage is at least 85% and cannot regress below the gate.
- Critical security modules meet their approved higher target.
- All required CI and security checks pass.
- No unresolved critical/high vulnerability is released without documented risk acceptance and compensating controls.
- Builds are reproducible from a clean checkout.
- Test logs and fixtures contain no real secrets or confidential data.

---

## Phase 10 — Data/Configuration Migration, Operations, and GA Release

### Objective

Provide a safe path from mem-store to ByteBox and release with complete operational support.

### Work

1. Create `bytebox config migrate`:
   - translate `MEMORY_STORE_` keys/YAML sections to `BYTEBOX_`;
   - report removed/renamed/unsafe settings;
   - never print secret values;
   - support dry-run and machine-readable output.
2. Create `bytebox database inspect`, `backup`, `migrate --dry-run`, `migrate`, `verify`, and `restore` commands.
3. Preserve source database until migration succeeds; write migrated data to a new path by default for the first major transition.
4. Validate record counts, schema version, vector dimensions, model identities, graph links, lifecycle states, and representative searches before cutover.
5. Create re-embedding workflow when the target embedding identity differs.
6. Publish:
   - installation/upgrade guide;
   - offline model provisioning guide;
   - Ollama and llama.cpp guides;
   - TLS/private CA/mTLS guide;
   - backup/restore and disaster-recovery guide;
   - health/metrics/logging guide;
   - performance tuning guide;
   - security hardening guide;
   - incident response and rollback runbooks.
7. Run release-candidate soak testing with realistic data and network fault injection.
8. Produce a signed release checklist and go/no-go review.
9. Release a beta/RC before general availability; remove compatibility code only according to the published policy.

### Deliverables

- configuration migration tool
- database backup/migration/verification/restore toolchain
- re-embedding migration tool
- complete operations/security/provider documentation
- RC soak report
- GA release notes and rollback plan

### Exit criteria

- A copy of representative mem-store data migrates, verifies, and rolls back successfully.
- Migration is non-destructive by default and requires a verified backup.
- Offline FastEmbed, Ollama HTTP/HTTPS, and llama.cpp embedding/reranking deployment scenarios pass documented acceptance tests.
- Production release gates in Section 11 all pass.
- Operators can diagnose startup, readiness, model, database, and TLS failures without access to confidential data.

---

## 11. Production Release Gates

ByteBox must not be declared generally available until all gates pass.

### Architecture

- No monolithic service remains.
- Dependency direction is enforced automatically.
- API, CLI, and library facade share application use cases.
- Embedded runtime enforces one process/one database owner.

### Functionality

- CRUD, hybrid retrieval, lifecycle, privacy, ingestion, import/export, and recovery behavior pass compatibility tests.
- FastEmbed local/offline embeddings and reranking work without internet access.
- Ollama embedding and llama.cpp embedding/reranking pass provider contracts.
- Model incompatibility is detected and cannot silently corrupt retrieval.

### Security

- Threat-model mitigations are implemented or formally risk-accepted.
- Secrets/content do not appear in logs, errors, status, state, metrics, or traces.
- Filesystem ingestion is rooted and traversal-safe.
- Provider endpoint policy prevents request-driven SSRF.
- Auth scopes protect high-risk operations.
- API and outbound TLS test matrices pass.
- Dependency/static/secret/container scans pass release policy.

### Reliability and operations

- Lifespan startup/shutdown and partial-failure tests pass.
- Liveness/readiness/status/state contracts are stable.
- Backup, migration, restore, and rollback are tested.
- Remote provider timeout, malformed output, outage, and circuit-breaker behavior is tested.
- Log `off` mode is verified.

### Quality

- At least 85% branch-aware coverage.
- No flaky required tests in the release qualification window.
- Strict typing/lint/format gates pass.
- Public API and configuration reference are generated and reviewed.

### Performance

- Database/index-backed candidate retrieval is verified.
- Approved latency, throughput, memory, startup, and retrieval-quality gates pass.
- No unbounded request, candidate, queue, retry, or concurrency path remains.

### Supply chain

- Dependencies are pinned/locked.
- SBOM is generated.
- Release package/image is built from CI.
- Release provenance/signature is generated where supported.
- Model manifests include identity, checksum, and license information.

---

## 12. Logging and Trace Event Specification

### 12.1 Example safe JSON event

```json
{
  "timestamp": "2026-07-18T22:10:31.481Z",
  "severity": "INFO",
  "event": "retrieval.completed",
  "service.name": "bytebox",
  "service.version": "1.0.0",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "operation": "memory.search",
  "provider": "fastembed",
  "candidate_count": 42,
  "result_count": 10,
  "duration_ms": 18.7,
  "outcome": "success"
}
```

Do not include the query, memory text, vectors, API token, model API key, database path, or raw exception.

### 12.2 Required events

- `application.starting`
- `application.ready`
- `application.stopping`
- `application.stopped`
- `database.opened`
- `database.migration.started/completed/failed`
- `provider.initialized`
- `provider.request.completed/failed`
- `retrieval.completed/failed`
- `ingestion.document.completed/failed`
- `ingestion.batch.completed/failed`
- `authorization.denied`
- `rate_or_limit.rejected`
- `health.readiness.changed`
- `security.configuration_rejected`

Events carry identifiers and counts only where safe. Error events use a stable safe error code; trace storage may retain a redacted stack trace according to environment and retention policy.

### 12.3 `off` semantics

When `BYTEBOX_LOGGING__LEVEL=off`:

- do not install normal ByteBox handlers;
- disable Uvicorn access logging;
- disable ByteBox log propagation;
- do not emit startup banners or provider progress logs;
- ensure third-party library logging is disabled or routed to a null handler under ByteBox's process policy;
- retain exit codes and health behavior as the operational signal;
- telemetry export is controlled independently and should default off unless explicitly enabled.

Automated tests must capture stdout/stderr during startup, requests, provider errors, and shutdown and assert no log records/output attributable to ByteBox.

---

## 13. Health, Status, and State Contracts

### 13.1 Liveness

```json
{
  "status": "alive",
  "service": "bytebox",
  "version": "1.0.0",
  "trace_id": "..."
}
```

No dependency checks and no confidential details.

### 13.2 Readiness

```json
{
  "status": "ready",
  "checks": [
    {"name": "database", "status": "ready", "code": "OK"},
    {"name": "schema", "status": "ready", "code": "OK"},
    {"name": "embedding_provider", "status": "ready", "code": "OK"},
    {"name": "reranker_provider", "status": "ready", "code": "OK"}
  ],
  "trace_id": "..."
}
```

Readiness returns `503` when a required check is not ready. Deep remote probes are configurable and have short timeouts/caching to avoid turning health checks into load amplification.

### 13.3 Status

Include only safe fields:

- ByteBox version/build commit/build time;
- uptime;
- runtime mode;
- schema version;
- database state, not absolute path;
- active provider type/model identity/dimension, excluding credentials and private endpoint details;
- TLS enabled and client-auth mode;
- logging level;
- migrations/re-embedding state;
- last successful backup time, if known.

### 13.4 Privileged state

May add:

- memory/chunk counts by safe aggregate;
- queue depth and saturation;
- circuit-breaker state;
- provider latency/error counters;
- storage free-space category;
- last safe error code/time;
- active operation counts.

It must still exclude content, vectors, tokens, connection strings, raw exception messages, certificate/private key paths, and raw model responses.

---

## 14. Security Control Checklist

### Configuration and secrets

- Use `SecretStr`-equivalent wrappers and secret references.
- Redact secret fields from `repr`, serialization, validation errors, and diagnostics.
- Reject weak/default production tokens.
- Support token rotation without restart where practical and safe.
- Set restrictive permissions on config, key, model, database, backup, and state files.

### API

- Constant-time token verification.
- Explicit route authorization scopes.
- Request body and field limits.
- Idempotency for retried mutations where relevant.
- CORS disabled by default.
- Trusted-host enforcement.
- Sanitized error envelope.
- API docs disabled/protected in production.
- Security headers appropriate to API/docs responses.

### Filesystem and ingestion

- Canonical root containment.
- Symlink policy.
- Approved file extensions/content types.
- Reject special devices/files.
- Size/count/chunk limits.
- State-managed manifests.
- Atomic writes and safe temporary files.
- No archive extraction without a separately reviewed safe extractor.

### Remote providers

- Config-only endpoint URLs.
- Host/CIDR allowlist and DNS/IP validation.
- Link-local/metadata address blocking.
- Redirects disabled.
- Timeouts/retries/circuit breakers/concurrency bounds.
- Certificate and hostname verification.
- Optional custom CA/mTLS.
- Response size and schema validation.
- No raw request/response body logging.

### Persistence and data

- Parameterized queries and constrained identifiers.
- Schema migration backup/dry-run/rollback.
- Database lock ownership and stale-lock policy tests.
- Atomic backup and restore verification.
- Privacy operations audited with identifiers/counts, not content.
- Hard delete separately authorized and disabled by default.

### Supply chain

- Dependency lock and audit.
- Secret scanning.
- Static analysis/CodeQL.
- SBOM.
- Container scan and non-root image.
- Read-only container filesystem except explicit data/model/state mounts.
- Model checksums, manifests, and license review.
- No unsafe serialized model formats or untrusted code execution.

---

## 15. Test Strategy and Coverage Allocation

The 85% requirement should be enforced at the repository level, but coverage must not be achieved by over-testing trivial DTOs while leaving critical logic weak.

Recommended target allocation:

| Area | Minimum target | Test emphasis |
|---|---:|---|
| Overall branch-aware coverage | 85% | CI hard gate |
| Auth, secrets, redaction, endpoint policy, path security | 95% | abuse cases and mutation tests |
| Config/TLS validation | 95% | combinatorial/property tests |
| Provider adapters/contracts | 90% | malformed/partial/timeout/dimension/TLS cases |
| Persistence/migrations | 90% | failure injection, rollback, version mismatch |
| Application use cases | 90% | behavior and authorization-independent rules |
| API/CLI adapters | 85% | contract and integration tests |
| Pure data models | appropriate | validation boundaries, avoid meaningless tests |

Exclusions must be narrow and documented. Generated files, type-only protocols, and platform-unreachable defensive branches may be considered; business, security, migration, and provider failure paths may not be excluded merely to meet the percentage.

---

## 16. Migration Strategy from mem-store

### 16.1 Naming migration

| mem-store | ByteBox |
|---|---|
| repository `mem-store` | repository `bytebox` |
| package `memory_store` | package `bytebox` |
| CLI `memory-store` | CLI `bytebox` |
| `MEMORY_STORE_...` | `BYTEBOX_...` |
| `MemoryStoreSettings` | `ByteBoxSettings` |
| API title `Memory Store` | `ByteBox` |
| data default `./data/memory_store` | new default `./data/bytebox` |

Do not silently point ByteBox at the old database path. Require explicit migration or opt-in compatibility path to prevent accidental mutation.

### 16.2 Data migration sequence

1. Stop mem-store and verify no active lock.
2. Inspect schema and embedding identity.
3. Create and verify a backup.
4. Run ByteBox migration dry-run.
5. Copy/migrate to a new ByteBox data path.
6. Validate schema, counts, links, lifecycle states, source metadata, and checksums.
7. Validate embedding dimension/model identity.
8. Re-embed if required using a resumable job.
9. Run representative retrieval-quality comparisons.
10. Start ByteBox in read-only verification mode.
11. Cut over writes.
12. Retain rollback data for the approved period.

### 16.3 Configuration migration

The migration command should output key names and actions but never resolved secret values:

```text
RENAMED  MEMORY_STORE_DATABASE__PATH -> BYTEBOX_DATABASE__PATH
RENAMED  MEMORY_STORE_API__LOCAL_API_TOKEN -> BYTEBOX_API__AUTH__TOKEN_ENV
ACTION   Move API token value into BYTEBOX_API_TOKEN
ADDED    BYTEBOX_MODELS__OFFLINE=true
REVIEW   Configure ingest roots before enabling REST ingestion
```

---

## 17. Principal Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Refactor changes retrieval ranking | User-visible quality regression | Characterization tests, fixed eval set, Recall@K/MRR/nDCG gates, phased candidate-query migration |
| Existing database/model identity is incomplete | Search incompatibility or re-embedding requirement | Inspect and fingerprint in Phase 0; backup; explicit compatibility policy; resumable re-embedding |
| FastEmbed offline behavior varies by version/model | Startup attempts network or fails unexpectedly | Pin tested version, explicit local path/cache/local-only/offline flags, network-denied CI test |
| Ollama lacks native cross-encoder reranking | Inconsistent semantics | Label optional LLM reranker distinctly; prefer llama.cpp/FastEmbed for native reranking; deterministic fallback |
| Private-network URLs create SSRF exposure | Internal network access beyond intended models | Config-only endpoints, host/CIDR allowlist, DNS/IP checks, redirects off, no request-supplied URL |
| TLS configuration is complex | Outage or unsafe verification bypass | Fail-closed validation, secure defaults, private-CA/mTLS examples, automated TLS matrix |
| `off` logging conflicts with diagnosing startup | Limited diagnostics | Preserve deterministic exit codes and health state; separate explicitly enabled telemetry; provide `bytebox doctor` command |
| Embedded DB cannot safely use multiple workers | Corruption/locking failures | Enforce one worker and document scaling boundary |
| New-repository rebrand loses provenance | Legal/compliance risk | Mandatory license/attribution review and provenance record before copying |
| 85% coverage achieved superficially | False confidence | Branch coverage, package risk targets, mutation tests, abuse/failure tests, code-review policy |
| Performance tuning alters correctness | Data or ranking defects | Measure first, isolate changes, differential tests, query-plan validation, rollback flags |

---

## 18. Recommended Implementation Order Summary

```text
0  Baseline, provenance, threat model, benchmarks
1  New ByteBox repository and clean rebrand
2  Modular application/domain decomposition
3  Lifespan-owned database and resource container
4  Provider contracts and offline FastEmbed
5  Ollama/llama.cpp providers and outbound TLS
6  API security, authorization, filesystem policy, inbound TLS
7  Structured logging, trace IDs, health/status/state
8  Indexed retrieval and runtime performance
9  85%+ coverage, CI, security, SBOM, release controls
10 Migration tooling, operations documentation, RC, GA
```

Phases 2 and 3 should complete before remote providers are added so new integrations land on the final lifecycle and dependency model. Security controls must be developed alongside provider/API work, not deferred until after feature completion. Performance changes should follow stable modular boundaries and baseline measurements. General availability follows migration and operations validation, not merely feature completion.

---

## 19. Definition of Done for ByteBox 1.0

ByteBox 1.0 is done when:

- the new repository is legally and operationally complete;
- branding is consistent across package, CLI, API, configuration, docs, logs, telemetry, and artifacts;
- the database is opened once during lifespan and closed safely;
- modules are cohesive and dependency boundaries are enforced;
- FastEmbed can run fully offline from verified local artifacts;
- Ollama embedding and llama.cpp embedding/reranking work over approved HTTP and HTTPS configurations;
- TLS is off by default and secure when enabled;
- authentication, authorization, filesystem containment, SSRF controls, safe errors, and secret handling pass security tests;
- trace IDs correlate API, application, database, and provider operations;
- `debug`, `info`, `warn`, and `off` behave exactly as documented;
- logs contain no secrets or memory/query content by default;
- liveness, readiness, status, state, and metrics are clear and safe;
- retrieval uses bounded indexed candidates and meets approved quality/performance gates;
- branch-aware coverage is at least 85%;
- CI/security/release gates pass;
- migration, backup, restore, rollback, and re-embedding workflows are tested;
- production installation and incident runbooks are complete.

---

## Appendix A — Suggested Application Lifespan Shape

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def bytebox_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_bytebox_settings()
    observability = configure_observability(settings)
    container = ApplicationContainer(settings=settings, observability=observability)

    try:
        await container.start()
        app.state.container = container
        container.readiness.mark_ready()
        yield
    finally:
        container.readiness.mark_not_ready()
        await container.close()
        app.state.container = None
```

Implementation requirements:

- `start()` and `close()` are idempotent.
- Partial startup tracks initialized resources and closes only those resources.
- Database open/migration occurs inside `start()`.
- Required provider initialization/warmup occurs before ready.
- Shutdown uses bounded drain time and cancellation-safe cleanup.
- Tests execute lifespan via a context-managed test client.

---

## Appendix B — Safe Error Taxonomy

Recommended stable error codes:

```text
BYTEBOX_CONFIG_INVALID
BYTEBOX_SECRET_UNAVAILABLE
BYTEBOX_DATABASE_OPEN_FAILED
BYTEBOX_DATABASE_LOCKED
BYTEBOX_SCHEMA_MISMATCH
BYTEBOX_MIGRATION_FAILED
BYTEBOX_MODEL_NOT_FOUND
BYTEBOX_MODEL_INTEGRITY_FAILED
BYTEBOX_MODEL_INCOMPATIBLE
BYTEBOX_PROVIDER_UNAVAILABLE
BYTEBOX_PROVIDER_TIMEOUT
BYTEBOX_PROVIDER_RESPONSE_INVALID
BYTEBOX_TLS_CONFIGURATION_INVALID
BYTEBOX_TLS_VERIFICATION_FAILED
BYTEBOX_ENDPOINT_NOT_ALLOWED
BYTEBOX_AUTHENTICATION_FAILED
BYTEBOX_AUTHORIZATION_DENIED
BYTEBOX_PATH_NOT_ALLOWED
BYTEBOX_REQUEST_LIMIT_EXCEEDED
BYTEBOX_INGESTION_FAILED
BYTEBOX_RETRIEVAL_FAILED
BYTEBOX_INTERNAL_ERROR
```

Public messages are stable and non-sensitive. Internal diagnostics are correlated through trace ID and sanitized telemetry.

---

## Appendix C — CI Workflow Matrix

Required pull-request jobs:

1. package/build metadata validation;
2. format and lint;
3. strict type checking;
4. architecture dependency tests;
5. unit tests with branch coverage;
6. ArcadeDB integration/lifespan tests;
7. API/CLI contract tests;
8. provider mock contract tests;
9. offline/no-network model tests where model fixtures permit;
10. TLS/security abuse tests;
11. dependency/license audit;
12. secret scan;
13. static security analysis/CodeQL;
14. package and container build;
15. SBOM generation;
16. benchmark smoke/regression check.

Scheduled or release jobs:

- real Ollama and llama.cpp integration matrix;
- full benchmark/retrieval evaluation;
- mutation tests on critical modules;
- container vulnerability scan;
- migration/restore exercise;
- signed release/provenance generation.

---

## Appendix D — Operational Metrics

Recommended metrics, with bounded labels and no user/content identifiers:

```text
bytebox_http_requests_total
bytebox_http_request_duration_seconds
bytebox_http_in_flight
bytebox_database_operation_duration_seconds
bytebox_database_errors_total
bytebox_retrieval_duration_seconds
bytebox_retrieval_candidates
bytebox_ingestion_documents_total
bytebox_ingestion_chunks_total
bytebox_ingestion_failures_total
bytebox_embedding_batch_size
bytebox_embedding_duration_seconds
bytebox_provider_requests_total
bytebox_provider_request_duration_seconds
bytebox_provider_failures_total
bytebox_provider_circuit_state
bytebox_work_queue_depth
bytebox_readiness
bytebox_storage_free_bytes
```

Never use memory ID, user ID, path, query, model endpoint, token, or free-form exception as metric labels.

---

## Appendix E — Reviewed Source Hotspots

| File | Review observation |
|---|---|
| `memory_store/service.py` | 1,666-line cross-capability service; eager database open; full-scope search materialization; broad administration/ingestion ownership |
| `memory_store/models.py` | Broad model surface requiring domain/API/config separation |
| `memory_store/config.py` | FastEmbed-only provider shape; plain API token; minimal logging setting; no HTTP/TLS/local-model configuration |
| `memory_store/cli.py` | Broad CLI requiring command modules and shared application use cases |
| `memory_store/api/main.py` | Lifespan closes but construction already opened resources; direct token comparison; raw exception text returned |
| `memory_store/api/routes.py` | One dependency set across all routes; caller-supplied filesystem and manifest paths; one health endpoint |
| `memory_store/embeddings/fastembed_provider.py` | No local/cache/offline options; private runtime introspection; raw initialization error included |
| `memory_store/retrieval/rerank.py` | Cross-encoder initialized per call; raw exception stored in candidate debug output |
| `pyproject.toml` | No coverage gate/security tooling visible in development dependencies/configuration |

---

## Appendix F — References

### Reviewed repository

1. mem-store repository: <https://github.com/doktordub/mem-store>
2. Project metadata: <https://github.com/doktordub/mem-store/blob/main/pyproject.toml>
3. Example configuration: <https://github.com/doktordub/mem-store/blob/main/config.example.yaml>
4. Core service: <https://github.com/doktordub/mem-store/blob/main/memory_store/service.py>
5. FastAPI application: <https://github.com/doktordub/mem-store/blob/main/memory_store/api/main.py>
6. REST routes: <https://github.com/doktordub/mem-store/blob/main/memory_store/api/routes.py>
7. FastEmbed provider: <https://github.com/doktordub/mem-store/blob/main/memory_store/embeddings/fastembed_provider.py>
8. Reranker: <https://github.com/doktordub/mem-store/blob/main/memory_store/retrieval/rerank.py>

### Official implementation guidance

9. FastAPI lifespan events: <https://fastapi.tiangolo.com/advanced/events/>
10. FastAPI lifespan testing: <https://fastapi.tiangolo.com/advanced/testing-events/>
11. FastEmbed repository/releases: <https://github.com/qdrant/fastembed> and <https://github.com/qdrant/fastembed/releases>
12. Ollama embedding API: <https://docs.ollama.com/api/embed>
13. Ollama embedding capability guidance: <https://docs.ollama.com/capabilities/embeddings>
14. llama.cpp server: <https://github.com/ggml-org/llama.cpp/tree/master/tools/server>
15. Uvicorn TLS settings: <https://www.uvicorn.org/settings/>
16. HTTPX SSL: <https://www.python-httpx.org/advanced/ssl/>
17. HTTPX clients/connection pooling: <https://www.python-httpx.org/advanced/clients/>
18. OpenTelemetry Python propagation: <https://opentelemetry.io/docs/languages/python/propagation/>
19. OWASP Logging Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
20. OWASP SSRF Prevention Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>
21. Coverage.py fail-under reporting: <https://coverage.readthedocs.io/en/latest/commands/cmd_reporting.html>

