# Metrics and Inventory Refactor Plan

## Review Basis

This plan is based on the live OpenAPI document at `http://127.0.0.1:8080/openapi.json` and the current implementation split across:

- `src/bytebox/api/routes.py`
- `src/bytebox/observability/diagnostics.py`
- `src/bytebox/domain/administration.py`
- `src/bytebox/services/administration.py`
- `src/bytebox/service.py`
- `src/bytebox/store.py`
- `src/bytebox/arcade/queries.py`

The goal is to make database inventory understandable to both users and agents through REST and the Python API without turning REST into the primary architecture.

## Current Endpoint Review

| Endpoint | Current role | Current payload | Overlap | Recommendation |
| --- | --- | --- | --- | --- |
| `/status` | Build, config, and runtime summary | version, uptime, provider configuration, TLS, logging, jobs | Low overlap with DB inventory | Keep narrow. Do not add high-cardinality DB inventory here. |
| `/state` | Operational diagnosis | top-line counters, memory status counts, memory type counts, queue, providers, storage, recent error, JSON metrics snapshot | Overlaps `/stats` for counts and `/metrics` for metric data | Keep operational. Reuse shared summary data, but do not turn this into the detailed inventory catalog. |
| `/metrics` | Monitoring scrape surface | OpenMetrics text | Overlaps `/state.metrics` conceptually, but not contract-wise | Keep scalar only. Never expose ID name lists or record previews here. |
| `/stats` | Database aggregate summary | total records, scope counts, status counts, type counts | Overlaps `/state` counters | Keep as the compact DB summary surface and source it from the richer inventory service. |

## Overlap Summary

The current overlap is concentrated in two places:

1. `/state` already embeds data that is also available from `/stats`.
2. `/state.metrics` and `/metrics` both expose metrics, but in different formats and with different intended consumers.

That overlap is acceptable for top-line operational counters, but it becomes a problem if detailed inventory is added directly to `/state` or `/metrics`.

## Key Findings

1. The current Python-first contract only exposes `stats()` and `health()` on the public facade. There is no public inventory or catalog method yet.
2. `/stats` is the cleanest existing anchor for database-only introspection because it already maps to `store.stats()` and does not mix in runtime state.
3. `/state` is already broader than `/stats` because it composes storage, queue, provider, error, and metrics data. It should remain an operational view, not become the source of truth for DB inventory.
4. `/metrics` should remain scrape-oriented. High-cardinality labels such as concrete `user_id`, `project_id`, or `agent_id` names should not be emitted there.
5. The requested reporting categories mostly align with the current `MemoryType` enum:
   - `user_preference`
   - `project_fact`
   - `task_state`
   - `conversation_summary`
   - `decision`
   - `observation`
   - `error_debug_note`
6. There is no first-class `episodic` memory type today. That must be resolved explicitly before exposing an `episodic memories` bucket.
7. `document_chunk` is a real stored type and should remain visible in inventory so users and agents can understand when document ingestion dominates the database.

## Recommended Target Split

### 1. Python-First Core Contract

Add a first-class inventory contract in the domain and service layer, then project it into REST.

Recommended new public API:

```python
store.inventory(
    detail="summary",          # "summary" | "full"
    include_names=False,
    names_limit=100,
    include_document_chunks=True,
)
```

Recommended service layering:

- Repository owns the aggregate queries.
- `AdministrationService` owns inventory assembly and policy decisions.
- `MemoryService` and `MemoryStore` expose the inventory API.
- REST only adapts the Python-first contract into HTTP.

Recommended domain models:

- `MemoryInventorySummary`
  - `total_records`
  - `scope_counts`
  - `status_counts`
  - `type_counts`
- `ScopeDimensionInventory`
  - `count`
  - `names`
  - `truncated`
  - `remaining`
- `ScopeInventory`
  - `distinct_scope_tuples`
  - `global_records`
  - `scoped_records`
  - `user_ids`
  - `project_ids`
  - `agent_ids`
- `MemoryTypeInventory`
  - `memory_type`
  - `display_name`
  - `count`
  - `status_counts`
  - `scope_counts`
  - `newest_updated_at`
  - `oldest_created_at`
- `MemoryInventoryReport`
  - `contract_version`
  - `generated_at`
  - `summary`
  - `scopes`
  - `memory_types`
  - `notes`

### 2. REST Projection

Primary recommendation:

- Keep `GET /stats` as the compact summary contract.
- Add `GET /inventory` as the new detailed JSON inventory contract.
- Keep `GET /state` operational and summary-oriented.
- Keep `GET /metrics` scrape-oriented.

Why this split is preferable:

- It avoids overloading `/state` with database-catalog concerns.
- It avoids making `/metrics` carry human-readable details.
- It preserves the existing `/stats` contract for current callers.
- It gives agents and operators a single JSON endpoint for answering "what is actually stored here?"

Recommended `GET /inventory` query parameters:

- `detail=summary|full`
- `include_names=true|false`
- `names_limit=<int>`
- `include_document_chunks=true|false`

Recommended auth level:

- `admin:read`

### 3. Metrics Split

Add scalar metrics for counts, not identity lists.

Recommended new OpenMetrics series:

- `bytebox_memory_records_total{memory_type="...",status="..."}`
- `bytebox_memory_scope_records_total{scope_kind="global|scoped"}`
- `bytebox_memory_scope_dimension_total{dimension="user_id|project_id|agent_id"}`
- `bytebox_memory_scope_tuples_total`
- `bytebox_inventory_report_generated_seconds`

Do not emit labels containing actual user, project, or agent names. Those belong only in JSON inventory payloads and the Python API.

## Requested Data Coverage

### Scope Inventory

The new inventory contract should expose:

- Number of distinct scope tuples in the database.
- Count of distinct `user_id` values and the list of names.
- Count of distinct `project_id` values and the list of names.
- Count of distinct `agent_id` values and the list of names.
- Top-line split between global records and scoped records.

Example shape:

```json
{
  "scopes": {
    "distinct_scope_tuples": 14,
    "global_records": 12,
    "scoped_records": 86,
    "user_ids": {
      "count": 2,
      "names": ["alice", "bob"],
      "truncated": false,
      "remaining": 0
    },
    "project_ids": {
      "count": 3,
      "names": ["docs", "architecture", "evals"],
      "truncated": false,
      "remaining": 0
    },
    "agent_ids": {
      "count": 1,
      "names": ["copilot"],
      "truncated": false,
      "remaining": 0
    }
  }
}
```

### Memory-Type Inventory

The new inventory contract should expose per-type counts for:

- User preferences
- Project facts
- Task states
- Conversation summaries
- Decision memories
- Observation memories
- Error/debug notes
- Document chunks

For each memory type, expose at least:

- Total count
- Status counts
- Global versus scoped counts
- Oldest created timestamp
- Newest updated timestamp

### Episodic Memory Gap

`episodic memories` is not a first-class stored type today. The refactor should explicitly choose one of these options in Phase 0:

1. Add a new `episodic` `MemoryType` and treat it as a first-class persisted category.
2. Define `episodic memories` as a derived reporting bucket backed by existing `conversation_summary` records plus metadata rules.

Recommendation: do not ship an `episodic` bucket until one of those rules is explicit and tested.

## Proposed REST and Python Contract Behavior

### `/stats`

Keep the current compact response for backward compatibility:

```json
{
  "total_records": 98,
  "scope_counts": {
    "global": 12,
    "scoped": 86
  },
  "status_counts": {
    "active": 92,
    "superseded": 4,
    "forgotten": 2
  },
  "type_counts": {
    "project_fact": 24,
    "document_chunk": 50
  }
}
```

Internally, this should become a projection of `inventory(detail="summary")` so the count logic is implemented once.

### `/inventory`

Add a new detailed JSON report for users, agents, dashboards, and debugging tools.

This is the right place for:

- scope-name lists
- per-type expanded details
- generated timestamp
- notes about derived categories such as `episodic`

This is not the place for:

- raw embeddings
- raw document content
- DB internals
- absolute file-system paths

### `/state`

Keep `/state` focused on operational state. It can continue to expose top-line memory counters, but those counters should be sourced from the same summary inventory builder rather than computed separately.

Recommended rule:

- `/state` may expose summary counts.
- `/state` should not expose identity lists.
- `/state` should point operators to `/inventory` for detailed database contents.

### `/metrics`

Keep `/metrics` purely scalar and scrape-safe. If `state.metrics` remains, it should contain only the same low-cardinality metric snapshot categories, not detailed inventory names.

## Phased Refactor Plan

### [DONE] Phase 0. Contract and Taxonomy Decisions

Objective: lock the inventory taxonomy before any transport changes.

Implemented decisions:

- `episodic` is treated as a derived reporting bucket over `conversation_summary` records and is not emitted until explicit metadata rules are implemented and tested.
- `GET /inventory` has a stable query contract of `detail`, `include_names`, `names_limit`, and `include_document_chunks`.
- `/stats` remains the compact backward-compatible summary surface.

Work:

1. [DONE] Define the new inventory models in the administration domain.
2. [DONE] Decide whether `episodic` is a first-class memory type or a derived reporting bucket.
3. [DONE] Decide the stable JSON contract for `GET /inventory`.
4. [DONE] Document that `/stats` remains compact and backward-compatible.
5. [DONE] Add explicit OpenAPI summaries and descriptions for `/status`, `/state`, `/metrics`, `/stats`, and the new `/inventory` surface.

Exit criteria:

- [DONE] Domain contract is approved.
- [DONE] `episodic` handling is explicit.
- [DONE] There is one documented source of truth for inventory semantics.

### [DONE] Phase 1. Python-First Inventory Aggregation

Objective: implement the core inventory logic below the REST adapter.

Work:

1. [DONE] Add repository aggregation methods for:
  - [DONE] distinct scope tuple counts
  - [DONE] distinct `user_id`, `project_id`, and `agent_id` counts
  - [DONE] bounded name lists for each scope dimension
  - [DONE] per-type counts and status breakdowns
  - [DONE] per-type global versus scoped counts
  - [DONE] per-type oldest and newest timestamps
2. [DONE] Add `AdministrationService.inventory(...)`.
3. [DONE] Add `MemoryService.inventory(...)`.
4. [DONE] Add `MemoryStore.inventory(...)`.
5. [DONE] Reimplement `stats()` as a summary projection of the new inventory builder.

Guardrails:

- Keep query logic in the repository layer.
- Keep response shaping in the service/domain layer.
- Keep REST unaware of query details.
- Cap list payloads with `names_limit` and truncation metadata.

Exit criteria:

- [DONE] Python callers can inspect inventory without using REST.
- [DONE] `stats()` and `inventory(detail="summary")` return consistent counts.

### [DONE] Phase 2. REST Exposure and Overlap Cleanup

Objective: expose the new inventory contract cleanly over HTTP.

Work:

1. [DONE] Add `GET /inventory` wired to `store.inventory(...)`.
2. [DONE] Keep `GET /stats` unchanged in shape, but source it from the shared inventory summary.
3. [DONE] Update `GET /state` to source its counters from the same summary inventory path.
4. [DONE] Keep `GET /status` unchanged except for clearer endpoint descriptions.
5. [DONE] Update docs and examples to show when to use each endpoint.

Recommended endpoint usage after this phase:

- `/status`: runtime/config summary
- `/state`: operational state and top-line counters
- `/stats`: compact DB summary
- `/inventory`: detailed DB inventory
- `/metrics`: scrape metrics

Exit criteria:

- [DONE] Detailed DB contents are available via REST.
- [DONE] No new data-source logic exists only inside the REST adapter.
- [DONE] The endpoint split is easy to explain to users and agents.

### [DONE] Phase 3. Metrics, Safety, and Usability

Objective: make the new inventory data safe and practical at scale.

Work:

1. [DONE] Add the new low-cardinality OpenMetrics series.
2. [DONE] Ensure name lists are bounded and clearly marked as truncated when capped.
3. [DONE] Add explicit redaction and sensitivity review for scope-name exposure.
4. [DONE] Exclude raw record text, embeddings, and absolute paths from inventory surfaces.
5. [DONE] Add generated timestamps so agents can reason about staleness.

Exit criteria:

- [DONE] `/metrics` remains scrape-safe.
- [DONE] `/inventory` remains admin-readable and bounded.
- [DONE] Large databases do not produce unbounded payloads.

### [DONE] Phase 4. Test and Documentation Completion

Objective: lock the new behavior with focused validation.

Work:

1. [DONE] Add unit tests for repository aggregation and service projections.
2. [DONE] Add REST tests for `/inventory`, `/stats`, and `/state` consistency.
3. [DONE] Add tests covering truncation behavior for large ID sets.
4. [DONE] Add tests for redaction and non-leakage of unsafe fields.
5. [DONE] Update examples for both Python and REST usage.

Suggested validation focus:

- administration service tests
- REST contract tests near `tests/test_phase10_rest_cli.py`
- observability tests near `tests/test_phase7_observability.py`

Exit criteria:

- [DONE] Python API and REST return the same inventory facts.
- [DONE] `/state` and `/stats` stay consistent.
- [DONE] Sensitive or high-cardinality data is not leaked through `/metrics`.

## Non-Goals

- Do not move inventory logic into FastAPI route functions.
- Do not add record-level content previews to `/metrics`.
- Do not overload `/status` with database catalog data.
- Do not silently invent an `episodic` category without explicit modeling.

## Final Recommendation

Use a new Python-first `inventory()` contract as the source of truth, keep `/stats` as the compact summary, add `/inventory` for detailed database contents, keep `/state` operational, and keep `/metrics` scalar only. That removes the current ambiguity without making REST the core architecture.