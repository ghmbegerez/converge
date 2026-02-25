# Implementation Backlog: Agentic Readiness

Date: 2026-02-23
Source plan: `docs/IMPLEMENTATION_PLAN_AGENTIC_READINESS.md`

## Conventions
- Priority: `P0` (critical), `P1` (high), `P2` (medium)
- Estimation is in ideal engineering days.
- Status starts as `TODO`.

## Plan coordination model

```
Intent(plan_id="plan-X", dependencies=[])         → PR #1
Intent(plan_id="plan-X", dependencies=["i-001"])   → PR #2
Intent(plan_id="plan-X", dependencies=["i-002"])   → PR #3
```

- **plan_id**: Optional grouping key. N intents sharing the same `plan_id` form a plan.
- **dependencies**: Existing `list[str]` field. Queue engine skips an intent if any dependency is not MERGED.
- **Humans**: 1 intent = 1 PR. No plan_id needed.
- **Agents**: N intents with plan_id + dependency chain. Each intent produces 1 PR.
- **No new entity**: Plan status is derived from its intents (`SELECT status, COUNT(*) FROM intents WHERE plan_id = ? GROUP BY status`).

## Phase 0 ADR deliverables

### AR-26 (P0) - Document ADR-004: Verification Debt KPI
- Status: DONE
- Objective: Formalize verification debt formula, time windows, thresholds, and tenant overrides.
- Changes:
  - Write ADR-004 under `docs/adr/`.
  - Define: `debt_score = f(pending_checks, queue_age, review_backlog, conflict_pressure)`.
  - Specify per-tenant threshold overrides and alert triggers.
- Dependencies: none
- Acceptance criteria:
  - ADR approved and linked in docs index.
  - Formula is unambiguous and testable.
  - Tenant override mechanism defined.
- Estimate: 0.5d
- Risks: premature optimization of formula before operational data.

### AR-27 (P0) - Document ADR-005: Security Adapter Contract
- Status: DONE
- Objective: Define scanner port, adapter contract, and normalized findings schema.
- Changes:
  - Write ADR-005 under `docs/adr/`.
  - Define: scanner interface, finding severity enum, evidence format, decision hooks.
  - Specify adapter lifecycle (register, invoke, collect, normalize).
- Dependencies: none
- Acceptance criteria:
  - ADR approved and linked in docs index.
  - Contract supports SAST, SCA, and secrets scanning categories.
  - Legacy shell fallback explicitly covered.
- Estimate: 0.5d
- Risks: over-generalization before real adapter implementation.

### AR-47 (P0) - Add plan_id field and dependency enforcement
- Status: DONE
- Objective: Add `plan_id` to Intent model and enforce dependencies in queue processing.
- Changes:
  - Add `plan_id: str | None` field to Intent model in `src/converge/models.py`.
  - Add `plan_id` column to intents table (nullable) in `src/converge/adapters/db/schema.py`.
  - Modify `process_queue()` in `src/converge/engine.py`: for each VALIDATED intent, if `intent.dependencies` is non-empty, skip if any dependency intent status is not MERGED.
  - Document ADR-006: Plan coordination model under `docs/adr/`.
- Dependencies: none
- Acceptance criteria:
  - `plan_id` persisted and queryable.
  - Queue engine skips intents whose dependencies are not all MERGED.
  - Intents without dependencies behave as before (no regression).
  - ADR-006 approved and linked in docs index.
- Estimate: 1.5d
- Risks: dependency cycles (mitigated: detect and reject at creation time).

## Epic A: Intent -> PR -> Commit explicit model (Phase 1)

### AR-01 (P0) - Define canonical linkage schema and migration plan
- Status: DONE
- Objective: Formalize explicit relationship model for intent/pr/commit.
- Changes:
  - Add `plan_id` nullable column to `intents` table.
  - Design migration for `intent_commit_links` (SHA tracking for head/base/merge commits).
  - Define keys, indexes, and uniqueness constraints.
- Dependencies: AR-47
- Acceptance criteria:
  - Schema design approved and documented.
  - Migration SQL includes rollback.
  - `plan_id` indexed for group queries.
- Estimate: 1d
- Risks: overfitting to GitHub-only shape.

### AR-02 (P0) - Implement storage contract for links
- Status: DONE
- Objective: Extend store port and base store to persist/retrieve links.
- Changes:
  - Update `src/converge/ports.py` with link operations.
  - Implement in `src/converge/adapters/base_store.py` and backend stores.
- Dependencies: AR-01
- Acceptance criteria:
  - CRUD for commit links available via port.
  - Store contract tests pass for sqlite/postgres parity.
- Estimate: 2d
- Risks: SQL dialect differences in upsert behavior.

### AR-03 (P0) - Add event taxonomy for linkage lifecycle
- Status: DONE
- Objective: Ensure auditability of link creation/updates/removals.
- Changes:
  - Add event types to `src/converge/models.py`:
  - `intent.linked.commit`, `intent.link.removed`.
- Dependencies: AR-01
- Acceptance criteria:
  - Events emitted on every link mutation.
  - Event payload includes minimal identifiers and provenance.
- Estimate: 0.5d
- Risks: inconsistent payload shape across call sites.

### AR-04 (P0) - Wire webhook ingestion to explicit link model
- Status: DONE
- Objective: Populate link entities from PR/push/merge_group events.
- Changes:
  - Update `src/converge/api/routers/webhooks.py`.
  - Persist commit links in addition to current `Intent.technical`.
  - Intent enters as READY (existing behavior preserved).
- Dependencies: AR-02, AR-03
- Acceptance criteria:
  - Webhook flows preserve existing behavior and create commit links.
  - Idempotency for duplicate deliveries maintained.
- Estimate: 1.5d
- Risks: duplicate link writes under retries.

### AR-05 (P0) - CLI/API read surfaces for links
- Status: DONE
- Objective: Expose explicit links to operators and automations.
- Changes:
  - Extend read endpoints and CLI output for intent detail/list.
  - Include `plan_id` in intent read output when present.
- Dependencies: AR-02, AR-04
- Acceptance criteria:
  - Intent read output includes explicit commit links and plan_id.
  - Backward compatibility with existing fields maintained.
- Estimate: 1d
- Risks: payload bloat in list endpoints.

### AR-06 (P0) - Backfill job from legacy technical metadata
- Status: DONE
- Objective: Migrate old records into explicit links.
- Changes:
  - Add one-time backfill script under `scripts/`.
- Dependencies: AR-02, AR-04
- Acceptance criteria:
  - Existing intents with `technical.pr_number`/`initial_base_commit` are linked.
  - Backfill is idempotent and logs summary.
- Estimate: 1d
- Risks: partial technical metadata quality.

## Epic B: Archaeology enhancer (Phase 1b)

### AR-07 (P0) - Enrich coupling with explicit link context
- Status: DONE
- Objective: Use intent-linked commit history to improve coupling fidelity.
- Changes:
  - Extend `src/converge/analytics.py` coupling loader to optionally use link tables.
- Dependencies: AR-02, AR-06
- Acceptance criteria:
  - Coupling source can combine git log + explicit linkage.
  - Fallback to current behavior remains intact.
- Estimate: 1.5d
- Risks: mixed-source weighting skew.

### AR-08 (P1) - Add archaeology freshness and provenance metadata
- Status: DONE
- Objective: Make risk evidence explainable (where coupling came from).
- Changes:
  - Add metadata in risk evidence payload:
  - source (`snapshot`, `git-log`, `linked-history`, `hybrid`), freshness timestamp.
- Dependencies: AR-07
- Acceptance criteria:
  - `risk.evaluated` evidence includes coupling provenance.
  - Operators can see stale coupling conditions.
- Estimate: 1d
- Risks: event payload drift.

### AR-09 (P1) - Add archaeology snapshot refresh command
- Status: DONE
- Objective: Operationalize snapshot lifecycle.
- Changes:
  - Add CLI command to refresh and validate snapshot consistency.
- Dependencies: AR-07
- Acceptance criteria:
  - Command regenerates snapshot and validates key counters.
  - Non-zero exit when invalid.
- Estimate: 1d
- Risks: long runtime on large repos.

## Epic C: Semantic processing (embeddings) (Phase 2)

### AR-10 (P0) - Define semantic canonical text builder
- Status: DONE
- Objective: Deterministic semantic input from intent + links + coupling summary.
- Changes:
  - Add module `src/converge/semantic/canonical.py`.
- Dependencies: AR-05, AR-07
- Acceptance criteria:
  - Same input produces same canonical text checksum.
  - Unit tests cover ordering and missing-field behavior.
- Estimate: 1d
- Risks: unstable token ordering.

### AR-11 (P0) - Add embedding provider abstraction
- Status: DONE
- Objective: Support pluggable embedding providers with test/noop adapter.
- Changes:
  - Add interface in `src/converge/semantic/embeddings.py`.
  - Add local deterministic test provider.
- Dependencies: AR-10
- Acceptance criteria:
  - Provider can be swapped by config.
  - Deterministic test mode available in CI.
- Estimate: 1.5d
- Risks: provider-specific vector dimension mismatch.

### AR-12 (P0) - Persist embedding metadata and vectors
- Status: DONE
- Objective: Store semantic fingerprints and embedding lineage.
- Changes:
  - Add schema/store methods for embedding records linked to intent.
- Dependencies: AR-11
- Acceptance criteria:
  - Records include model, dimension, checksum, version, generated_at.
  - Recompute on semantic changes only.
- Estimate: 2d
- Risks: storage growth and migration compatibility.

### AR-13 (P1) - Add reindex pipeline + CLI
- Status: DONE
- Objective: Allow controlled reindex on model/version changes.
- Changes:
  - Add CLI command for tenant/global reindex.
- Dependencies: AR-12
- Acceptance criteria:
  - Reindex supports dry-run and batch mode.
  - Progress and failure summary logged.
- Estimate: 1d
- Risks: long-running jobs without checkpoints.

### AR-14 (P1) - API visibility for semantic status
- Status: DONE
- Objective: Expose semantic readiness per intent/tenant.
- Changes:
  - Add endpoint(s) for embedding status/coverage.
- Dependencies: AR-12
- Acceptance criteria:
  - API returns indexed %, stale %, failed %.
  - Includes last model/version used.
- Estimate: 1d
- Risks: high-cost aggregate queries.

## Epic D: Human/agent distinction in native policy (Phase 3)

### AR-15 (P0) - Add `origin_type` to intent model and storage
- Status: DONE
- Objective: Track provenance natively (`human`, `agent`, `integration`).
- Changes:
  - Update `src/converge/models.py` and store schema/mapping.
- Dependencies: AR-01
- Acceptance criteria:
  - All new intents include origin.
  - Existing intents backfilled to sensible default.
- Estimate: 1.5d
- Risks: default classification ambiguity.

### AR-16 (P0) - Populate origin in ingestion paths
- Status: DONE
- Objective: Set origin in CLI/webhook/agent creation flows.
- Changes:
  - Update intent creation in CLI and webhook routers.
- Dependencies: AR-15
- Acceptance criteria:
  - CLI intents default to `human` unless explicit override.
  - Webhook intents default to `integration`.
  - Agent-created flows set `agent`.
- Estimate: 1d
- Risks: hidden creation paths not updated.

### AR-17 (P1) - Extend policy profiles by origin
- Status: DONE
- Objective: Support origin-specific checks and thresholds.
- Changes:
  - Update `src/converge/policy.py` config model and evaluator.
- Dependencies: AR-16
- Acceptance criteria:
  - Policy can branch by origin_type.
  - Config fallback to current behavior if origin rules absent.
- Estimate: 1.5d
- Risks: config complexity explosion.

## Epic E: Inter-origin semantic conflict detection (Phase 4)

Scope: conflicts **between** intents from different origins/plans targeting the same branch. Intra-plan coherence is the generator's responsibility — Converge trusts the generator for conflicts within a plan, just as it trusts whoever approved the intent.

Converge is the only point with visibility of all intents in flight from all origins. Without this, the intent model does not scale beyond a single actor.

### AR-18 (P0) - Candidate generation by embedding similarity
- Status: DONE
- Objective: Efficiently shortlist potential inter-origin semantic conflicts.
- Changes:
  - Add conflict service module under `src/converge/semantic/conflicts.py`.
  - Compare intents from **different** `plan_id` values (or null plan_id) targeting the same branch.
  - Intents sharing the same `plan_id` are excluded from comparison (intra-plan coherence is the generator's responsibility).
  - Invoke during validation or as a pre-check before queue processing.
- Dependencies: AR-12
- Acceptance criteria:
  - Candidate selection bounded by top-k and similarity threshold.
  - Same-plan intents are never flagged against each other.
  - Runtime remains predictable under load.
- Estimate: 1.5d
- Risks: O(n^2) behavior if thresholds too loose.

### AR-19 (P0) - Conflict scoring heuristics v1
- Status: DONE
- Objective: Combine semantic similarity with structural signals for inter-origin conflicts.
- Changes:
  - Combine embeddings + branch target overlap + coupling overlap.
  - Score only applies to intents from independent origins.
- Dependencies: AR-18, AR-07
- Acceptance criteria:
  - Score and rationale are emitted for each conflict candidate.
  - Includes confidence and top contributing signals.
  - Scoring excludes intra-plan pairs.
- Estimate: 2d
- Risks: false positives/negatives balance.

### AR-20 (P0) - Conflict eventing and resolution integration
- Status: DONE
- Objective: Emit inter-origin conflict findings and gate intent validation.
- Changes:
  - Add events in `src/converge/models.py` and emit during conflict evaluation.
  - Conflict resolution recorded in intent (resolution_type: `accepted`, `deferred`, `merged_scope`).
  - Shadow mode: detect and log conflicts without blocking. Enforced mode: block intent at validation.
- Dependencies: AR-19
- Acceptance criteria:
  - Events: `intent.semantic_conflict.detected`, `intent.semantic_conflict.resolved`.
  - In shadow mode: conflicts are detected, logged, and auto-resolved (no blocking).
  - In enforced mode: unresolved inter-origin conflicts block validation.
  - Conflict resolution outcome persisted in intent.
- Estimate: 1.5d
- Risks: event noise volume.

### AR-21 (P1) - API/dashboard exposure for semantic conflicts
- Status: DONE
- Objective: Operational visibility for inter-origin conflict triage.
- Changes:
  - Add endpoints and dashboard cards for open conflicts.
- Dependencies: AR-20
- Acceptance criteria:
  - Can filter by tenant, severity, confidence, origin.
  - Shows explainability payload including conflicting origins/plans.
- Estimate: 1d
- Risks: dashboard query performance.

## Epic F: Verification debt E2E (Phase 5)

### AR-28 (P0) - Add verification debt projection module
- Status: DONE
- Objective: Compute verification debt score from pending checks, queue age, and review backlog.
- Changes:
  - Add `src/converge/projections/verification.py`.
  - Implement score formula from ADR-004.
  - Integrate with existing projection refresh cycle.
- Dependencies: AR-26
- Acceptance criteria:
  - Score computable per tenant and globally.
  - Unit tests cover boundary cases (empty queue, max backlog, zero checks).
  - Score formula matches ADR-004 specification.
- Estimate: 2d
- Risks: formula instability before calibration with real data.

### AR-29 (P0) - Add verification debt API endpoint and CLI
- Status: DONE
- Objective: Expose debt score to operators and automations.
- Changes:
  - Add endpoint: `GET /api/verification/debt` (per-tenant, global).
  - Add CLI command: `converge verification debt [--tenant T]`.
  - Include breakdown by contributing factors.
- Dependencies: AR-28
- Acceptance criteria:
  - API returns current score, trend (delta vs last window), and breakdown.
  - CLI output is JSON-structured.
  - Backward compatible (no changes to existing endpoints).
- Estimate: 1d
- Risks: payload bloat in breakdown detail.

### AR-30 (P1) - Add debt compliance integration and alerts
- Status: DONE
- Objective: Trigger compliance events when debt exceeds configured thresholds.
- Changes:
  - Extend `src/converge/projections/compliance.py` with debt threshold checks.
  - Emit events: `compliance.debt.warning`, `compliance.debt.critical`.
  - Support per-tenant threshold overrides.
- Dependencies: AR-28
- Acceptance criteria:
  - Threshold breach emits event with debt score, threshold, and tenant.
  - Threshold recovery emits `compliance.debt.recovered`.
  - Tests cover crossing and recovery scenarios.
- Estimate: 1d
- Risks: alert fatigue from oscillating scores.

### AR-31 (P1) - Add verification debt dashboard view
- Status: DONE
- Objective: Visual debt tracking for operations teams.
- Changes:
  - Add dashboard card showing current debt score, trend line, and per-tenant breakdown.
  - Highlight SLA-breaching tenants.
- Dependencies: AR-29
- Acceptance criteria:
  - Dashboard shows debt score with 1h/6h/24h trend.
  - Per-tenant drill-down available.
  - Color-coded thresholds (green/yellow/red).
- Estimate: 1d
- Risks: dashboard query performance on high-cardinality tenant data.

## Epic G: Human review orchestration (Phase 6)

### AR-32 (P0) - Define review task entity and schema
- Status: DONE
- Objective: Model human review as a first-class entity with lifecycle.
- Changes:
  - Add `review_tasks` table: `id, intent_id, reviewer, status, priority, risk_level, sla_deadline, created_at, assigned_at, completed_at, escalated_at, resolution, notes`.
  - Add migration with rollback.
  - Add model in `src/converge/models.py`.
- Dependencies: AR-47
- Acceptance criteria:
  - Schema design approved and documented.
  - Migration SQL includes rollback.
  - Status enum: `pending`, `assigned`, `in_review`, `escalated`, `completed`, `cancelled`.
- Estimate: 1d
- Risks: over-normalization of reviewer identity.

### AR-33 (P0) - Add review task events and lifecycle
- Status: DONE
- Objective: Ensure auditability of every review state transition.
- Changes:
  - Add event types: `review.requested`, `review.assigned`, `review.reassigned`, `review.escalated`, `review.completed`, `review.cancelled`.
  - Emit events on every lifecycle transition.
- Dependencies: AR-32
- Acceptance criteria:
  - Events emitted on every status change.
  - Event payload includes intent_id, reviewer, old_status, new_status, reason.
  - Events are append-only and queryable via existing event infrastructure.
- Estimate: 1d
- Risks: inconsistent payload shape across transition types.

### AR-34 (P0) - Implement assignment and SLA rules engine
- Status: DONE
- Objective: Auto-assign reviews and detect SLA breaches.
- Changes:
  - Add assignment rules based on: risk level, semantic conflict confidence, queue age, reviewer load.
  - SLA calculation: deadline = created_at + sla_window(risk_level).
  - Breach detection in projection cycle.
  - Escalation trigger when SLA is X% elapsed without completion.
  - Review triggered by policy evaluation or conflict detection results.
- Dependencies: AR-33
- Acceptance criteria:
  - Critical-risk intents get shortest SLA window.
  - SLA breach emits `review.sla.breached` event.
  - Escalation creates new event and updates review task status.
  - Rules configurable per tenant.
- Estimate: 2.5d
- Risks: SLA window calibration without historical data.

### AR-35 (P1) - Add review task API and CLI operations
- Status: DONE
- Objective: Expose review task management to operators and integrations.
- Changes:
  - Add endpoints: `POST /api/reviews`, `GET /api/reviews`, `PATCH /api/reviews/{id}`.
  - Operations: create, assign, reassign, escalate, complete, cancel.
  - Add CLI commands: `converge review list`, `converge review assign`, `converge review complete`.
- Dependencies: AR-34
- Acceptance criteria:
  - CRUD operations available via API and CLI.
  - Filtering by status, reviewer, tenant, SLA breach.
  - Backward compatible (no changes to existing endpoints).
- Estimate: 2d
- Risks: authorization complexity for reviewer-scoped operations.

### AR-36 (P1) - Add review task dashboard views
- Status: DONE
- Objective: Operational visibility for review workload and SLA compliance.
- Changes:
  - Dashboard cards: open reviews by status, aging distribution, SLA breach count, reviewer load.
  - Drill-down: per-reviewer queue, per-tenant review backlog.
- Dependencies: AR-35
- Acceptance criteria:
  - Dashboard shows real-time review queue status.
  - SLA breaches highlighted with time-over indicator.
  - Reviewer load distribution visible.
- Estimate: 1.5d
- Risks: dashboard query performance with large review backlogs.

## Epic H: Native security adapters (Phase 7)

### AR-37 (P0) - Add security scanner port and findings schema
- Status: DONE
- Objective: Define pluggable security scanner interface and normalized output.
- Changes:
  - Add scanner protocol in `src/converge/ports.py`.
  - Define findings schema: `id, scanner, category (sast|sca|secrets), severity (critical|high|medium|low|info), file, line, rule, evidence, confidence`.
  - Add finding events: `security.scan.started`, `security.scan.completed`, `security.finding.detected`.
- Dependencies: AR-27
- Acceptance criteria:
  - Port contract supports async and sync scanner invocation.
  - Findings schema covers SAST, SCA, and secrets categories.
  - Event payload includes scan duration and finding count.
- Estimate: 1.5d
- Risks: schema too rigid for diverse scanner outputs.

### AR-38 (P0) - Implement SAST, SCA, and secrets scanner adapters
- Status: DONE
- Objective: Provide concrete scanner implementations.
- Changes:
  - Add adapters under `src/converge/adapters/security/`:
    - `bandit_adapter.py` (SAST for Python).
    - `pip_audit_adapter.py` (SCA for Python dependencies).
    - `gitleaks_adapter.py` (secrets detection).
    - `shell_adapter.py` (legacy `make security-scan` fallback).
  - Each adapter normalizes output to findings schema.
- Dependencies: AR-37
- Acceptance criteria:
  - Each adapter invokes tool, parses output, and returns normalized findings.
  - Graceful degradation when tool is not installed (skip with warning).
  - Legacy shell adapter preserves current behavior.
  - Unit tests with fixture outputs for each scanner.
- Estimate: 3d
- Risks: tool version compatibility and output format changes.

### AR-39 (P0) - Integrate security findings into policy evaluation
- Status: DONE
- Objective: Policy can enforce or soft-enforce based on security scan results.
- Changes:
  - Extend `src/converge/policy.py` with security gate.
  - Decision hooks: `block` (critical/high findings), `warn` (medium), `info` (low).
  - Configurable severity thresholds per risk profile.
  - Security gate runs after existing gates (verification, containment, entropy).
- Dependencies: AR-38
- Acceptance criteria:
  - Policy blocks intents with critical findings (configurable threshold).
  - Warning-level findings are logged but do not block.
  - Security gate results included in policy verdict payload.
  - Existing policy behavior unchanged when no scanner configured.
- Estimate: 2d
- Risks: false-positive blocking in early rollout.

### AR-40 (P1) - Add security findings API and dashboard
- Status: DONE
- Objective: Operational visibility for security findings and scan history.
- Changes:
  - Add endpoints: `GET /api/security/findings`, `GET /api/security/scans`.
  - Filtering by severity, category, scanner, intent, tenant.
  - Dashboard cards: finding counts by severity, scan coverage, trend.
- Dependencies: AR-39
- Acceptance criteria:
  - API supports filtering by severity, category, and tenant.
  - Dashboard shows finding distribution and scan coverage percentage.
  - Drill-down to individual findings with evidence.
- Estimate: 1.5d
- Risks: high-volume finding storage and query performance.

## Epic I: Adaptive intake and backpressure (Phase 8)

### AR-41 (P0) - Add intake policy pre-check
- Status: DONE
- Objective: Control intake rate based on system health signals.
- Changes:
  - Extend existing policy evaluation with an intake pre-check in `src/converge/policy.py`.
  - Pre-check evaluates at intent creation time (before or during READY).
  - Modes: `open` (normal), `throttle` (rate-limited), `pause-critical-only` (only critical-risk intents accepted).
  - Inputs: verification debt score, semantic conflict pressure, queue health metrics.
  - Mode transitions based on configurable thresholds.
- Dependencies: AR-28, AR-20
- Acceptance criteria:
  - Pre-check evaluates before intent enters pipeline.
  - Mode transitions are automatic based on input signals.
  - Manual mode override available via API.
  - Rejected intents receive clear reason in response.
- Estimate: 2d
- Risks: incorrect threshold calibration causing unnecessary throttling.

### AR-42 (P0) - Add intake eventing and telemetry
- Status: DONE
- Objective: Full auditability of intake decisions.
- Changes:
  - Emit events: `intake.accepted`, `intake.throttled`, `intake.rejected`, `intake.mode.changed`.
  - Add metrics: throttle rate, rejection count, mode duration.
  - Include reason and contributing signals in event payload.
- Dependencies: AR-41
- Acceptance criteria:
  - Every intake decision is evented with full context.
  - Mode transitions are evented with old/new mode and trigger reason.
  - Metrics are accessible via health endpoint.
- Estimate: 1d
- Risks: event volume spike during throttle mode.

### AR-43 (P1) - Add intake configuration API and CLI
- Status: DONE
- Objective: Operational control of intake behavior.
- Changes:
  - Add endpoints: `GET /api/intake/status`, `POST /api/intake/mode`.
  - Per-tenant mode overrides.
  - CLI commands: `converge intake status`, `converge intake set-mode <mode> [--tenant T]`.
- Dependencies: AR-42
- Acceptance criteria:
  - Operators can view current mode, thresholds, and contributing signals.
  - Manual mode override persists until threshold-based auto-transition.
  - CLI output is JSON-structured.
- Estimate: 1d
- Risks: mode conflict between auto-transition and manual override.

## Matrix gap coverage

### AR-44 (P2) - Add event batch tamper-evidence chain
- Status: DONE
- Objective: Provide cryptographic integrity guarantee for event history.
- Changes:
  - Add hash chain: each event batch includes SHA-256 of previous batch hash + current batch content.
  - Store chain head in `event_chain_state` table.
  - Add verification command: `converge audit verify-chain`.
- Dependencies: none
- Acceptance criteria:
  - Every event batch includes `prev_hash` and `batch_hash`.
  - Verification command detects any gap or tampering in chain.
  - Chain initialization is backward-compatible (existing events get genesis hash).
- Estimate: 1.5d
- Risks: performance overhead on high-frequency event writes.

### AR-45 (P2) - Add code-area ownership and SoD matrix
- Status: DONE
- Objective: Enable separation-of-duties enforcement by code area.
- Changes:
  - Add `code_ownership` configuration model: maps path patterns to owners/teams.
  - Extend agent policy to enforce SoD: agent cannot approve its own code area.
  - Add ownership validation in policy evaluation.
- Dependencies: AR-15
- Acceptance criteria:
  - Ownership rules configurable via JSON/YAML file.
  - Agent policy respects code-area boundaries.
  - SoD violations emit `policy.sod.violation` event.
  - Missing ownership defaults to permissive (no block).
- Estimate: 2d
- Risks: ownership config maintenance burden.

### AR-46 (P2) - Add pre-PR evaluation harness
- Status: DONE
- Objective: Enable prompt/eval loop before intent creation to catch issues early.
- Changes:
  - Add harness module at `src/converge/harness.py`.
  - Evaluate intent quality signals before formal creation.
  - Shadow mode: log evaluation results without blocking.
  - Integration point for external prompt evaluation tools.
- Dependencies: AR-12
- Acceptance criteria:
  - Harness can evaluate intent against semantic similarity to existing intents.
  - Shadow mode logs results as events without blocking creation.
  - API endpoint for explicit pre-evaluation: `POST /api/intents/evaluate`.
  - Configurable evaluation rules.
- Estimate: 2d
- Risks: latency overhead on intent creation path.

## Cross-cutting hardening backlog (Phase 9)

### AR-22 (P1) - Verification debt integration with inter-origin conflict pressure
- Status: DONE
- Objective: Incorporate inter-origin semantic conflict load into debt score.
- Dependencies: AR-20, AR-28
- Acceptance criteria:
  - Debt projection includes inter-origin conflict pressure component behind flag.
- Estimate: 1d
- Risks: over-penalization early in rollout.

### AR-23 (P1) - Feature flags and rollout controls
- Status: DONE
- Objective: Safe progressive delivery.
- Changes:
  - Flags: `intent_links`, `archaeology_enhanced`, `intent_semantics`,
    `origin_policy`, `semantic_conflicts_shadow`,
    `verification_debt`, `review_tasks`, `security_adapters`, `intake_control`,
    `plan_coordination`.
- Dependencies: AR-02, AR-07, AR-12, AR-17, AR-20, AR-28, AR-33, AR-37, AR-41, AR-47
- Acceptance criteria:
  - Each feature can be toggled per environment.
  - Flags cover all Phases 1-8 capabilities.
  - `plan_coordination` flag controls dependency enforcement activation.
- Estimate: 1.5d
- Risks: config drift across environments.

### AR-24 (P1) - Performance and load validation
- Status: DONE
- Objective: Ensure new features do not regress queue/API SLAs.
- Dependencies: AR-21, AR-23, AR-31, AR-36, AR-40, AR-43
- Acceptance criteria:
  - Load tests show no critical regressions vs baseline.
  - Covers new endpoints from Phases 5-8.
  - Tests validate dependency resolution overhead under load.
- Estimate: 3d
- Risks: inadequate baseline snapshots.

### AR-25 (P1) - Runbook and operational playbooks
- Status: DONE
- Objective: Make operations executable by on-call.
- Dependencies: AR-23, AR-24
- Acceptance criteria:
  - Runbook includes reindex, stale archaeology handling, conflict triage steps.
  - Covers review task escalation, security finding triage, intake mode management.
  - Documents plan coordination model and dependency troubleshooting.
- Estimate: 2d
- Risks: docs drift after implementation changes.

## Recommended execution sequence
1. AR-47, AR-26, AR-27 (Phase 0 ADRs)
2. AR-01 -> AR-06 (Phase 1: data foundation)
3. AR-07 -> AR-09 (Phase 1b: archaeology)
4. AR-10 -> AR-14 (Phase 2: semantic processing)
5. AR-15 -> AR-17 (Phase 3: human/agent distinction)
6. AR-18 -> AR-21 (Phase 4: semantic conflicts)
7. AR-32 -> AR-36 (Phase 6: review orchestration)
8. AR-41 -> AR-43 (Phase 8: adaptive intake)
9. AR-28 -> AR-31 (Phase 5: verification debt)
10. AR-37 -> AR-40 (Phase 7: security adapters)
11. AR-44 -> AR-46 (matrix gap closure)
12. AR-22 -> AR-25 (Phase 9: cross-cutting hardening)

## Milestones
- M0 (architecture): AR-47, AR-26, AR-27 complete (ADRs approved).
- M1 (data foundation): AR-01..AR-06 complete.
- M2 (structural intelligence): AR-07..AR-09 complete.
- M3 (semantic foundation): AR-10..AR-14 complete.
- M4 (governance by origin): AR-15..AR-17 complete.
- M5 (semantic conflict detection): AR-18..AR-21 complete.
- M6 (review orchestration): AR-32..AR-36 complete.
- M7 (adaptive intake): AR-41..AR-43 complete.
- M8 (verification debt): AR-28..AR-31 complete.
- M9 (security adapters): AR-37..AR-40 complete.
- M10 (matrix gaps closed): AR-44..AR-46 complete.
- M11 (production hardening): AR-22..AR-25 complete.

## Estimation summary

| Phase | Items | Estimate (days) |
|---|---|---|
| Phase 0 (ADRs) | AR-26, AR-27, AR-47 | 2.5d |
| Phase 1 (Intent links) | AR-01..AR-06 | 7d |
| Phase 1b (Archaeology) | AR-07..AR-09 | 3.5d |
| Phase 2 (Semantic) | AR-10..AR-14 | 6.5d |
| Phase 3 (Origin) | AR-15..AR-17 | 4d |
| Phase 4 (Conflicts) | AR-18..AR-21 | 6d |
| Phase 5 (Debt) | AR-28..AR-31 | 5d |
| Phase 6 (Review) | AR-32..AR-36 | 8d |
| Phase 7 (Security) | AR-37..AR-40 | 8d |
| Phase 8 (Intake) | AR-41..AR-43 | 4d |
| Matrix gaps | AR-44..AR-46 | 5.5d |
| Phase 9 (Hardening) | AR-22..AR-25 | 7.5d |
| **Total** | **47 items** | **67d (~9-10 weeks)** |
