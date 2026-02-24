# Implementation Plan: Agentic Review Readiness

Date: 2026-02-23

## Goal
Close current `PARCIAL` gaps while preserving low accidental complexity and healthy system entropy.

## Baseline
- Core architecture is stable (`engine`, `event_log`, `policy`, `projections`, `worker`).
- Readiness matrix exists at `docs/READINESS_AGENTIC_REVIEW_MATRIX.md`.
- API readiness initialization fix is already applied in `src/converge/api/__init__.py`.

## Strategic priorities added (requested)
1. Explicit model `Intent -> PR -> Commit`.
2. Semantic processing for intents (embeddings over `problem_statement` and `objective`).
3. Semantic conflict detector between concurrent intents.
4. Native policy distinction for human-origin vs agent-origin intents.

## Plan coordination model

For agents producing multi-phase changes, the system uses **plan_id + dependencies** — not new states or loops:

```
Intent(plan_id="plan-X", dependencies=[])         → PR #1 (infra)
Intent(plan_id="plan-X", dependencies=["i-001"])   → PR #2 (backend)
Intent(plan_id="plan-X", dependencies=["i-002"])   → PR #3 (frontend)
```

- **plan_id**: Nullable grouping key on Intent. N intents sharing the same `plan_id` form a plan.
- **dependencies**: Existing `list[str]` field. Queue engine skips an intent if any dependency is not MERGED.
- **No new entity**: Plan is not a model or table. Plan status is derived from its intents.
- **Humans**: 1 intent = 1 PR. `plan_id` is null.
- **Agents**: N intents with `plan_id` + dependency chain. Each intent is independently validated, queued, and merged.
- **Pipeline unchanged**: Each intent goes through `READY → VALIDATED → QUEUED → MERGED` independently.

### ADR-006: Plan coordination model
- Define `plan_id` field semantics, dependency enforcement rules, and cycle detection.
- Define how `process_queue()` evaluates dependencies before processing an intent.
- Why: enable multi-phase agent workflows without adding lifecycle complexity.

### Intent lifecycle (unchanged)

```
READY → VALIDATED → QUEUED → MERGED | REJECTED
```

No new states. Each intent — whether standalone or part of a plan — follows the same pipeline.

## Phase 0: Architecture decisions and constraints (2-3 days)
1. ADR-001: Intent linkage model
   - Define first-class entities for commit linkage.
   - Why: foundational data integrity for all higher-order analysis.
2. ADR-002: Semantic intent processing
   - Define canonical text, embedding provider abstraction, versioning strategy.
   - Why: semantic capability without vendor lock-in.
3. ADR-003: Origin-aware governance
   - Define human vs agent provenance and policy effects.
   - Why: governance and risk boundaries by source type.
4. ADR-004: Verification debt KPI
   - Define formula, windows, thresholds, tenant overrides.
   - Why: operational control metric.
5. ADR-005: Security adapters
   - Define scanner port + adapter contract + normalized findings schema.
   - Why: avoid hard dependence on shell-based checks.
6. ADR-006: Plan coordination model
   - Define `plan_id` field, dependency enforcement in `process_queue()`, cycle detection.
   - Why: enable multi-phase agent workflows using existing pipeline.
7. Complexity guardrails
   - Max module growth, no duplicated policy logic in routers, event log remains source of truth.
   - Why: preserve architecture entropy.

Definition of done:
- ADRs approved and linked in docs.
- Data model and event taxonomy stabilized before implementation.

## Phase 1: Explicit Intent -> PR -> Commit model (5-7 days)
1. Schema additions
   - Add `plan_id` nullable column to `intents` table.
   - Add linkage table:
   - `intent_commit_links(intent_id, repo, sha, role=head|base|merge, observed_at)`
2. Event model
   - Add events:
   - `intent.linked.commit`, `intent.link.removed`.
3. API and query surface
   - Extend read endpoints to return explicit links and `plan_id`.
   - Keep backward compatibility with `Intent.technical`.
4. Migration and compatibility
   - Backfill from existing `technical.pr_number`, `technical.initial_base_commit`, webhook payloads.
5. Tests
   - Migration, CRUD, idempotency, webhook update behavior.

Definition of done:
- One intent can map to many commits explicitly.
- plan_id enables grouping queries.
- No loss of current webhook behavior.
- Legacy consumers remain functional.

## Phase 2: Semantic processing for intents (embeddings) (5-8 days)
1. Canonical semantic text
   - Build deterministic text from `problem_statement`, `objective`, and optional technical hints.
2. Embedding pipeline
   - New module: `src/converge/semantic/embeddings.py`.
   - Provider interface + local noop/test provider.
3. Storage
   - Persist embedding vector metadata:
   - model, dimension, checksum, generated_at, semantic_text_version.
4. Re-index strategy
   - Recompute on semantic field changes, model upgrade, or force-reindex command.
5. API and CLI
   - Add semantic status visibility endpoint and CLI command for reindex.
6. Tests
   - Determinism, update triggers, provider fallback, schema roundtrip.

Definition of done:
- Every active intent has semantic fingerprint metadata.
- Embeddings are reproducible and versioned.
- No runtime dependency on a single vendor.

## Phase 3: Native human/agent distinction in policy (3-5 days)
1. Provenance modeling
   - Add explicit `origin_type` on intent: `human`, `agent`, `integration`.
   - Populate from CLI/webhook/agent workflows.
2. Policy integration
   - Extend policy config with per-origin thresholds and required checks.
3. Authorization coupling
   - Agent policy can enforce stricter limits for agent-origin intents.
4. Tests
   - Ensure same intent content can produce different policy outcomes by origin when configured.

Definition of done:
- Origin is explicit and queryable.
- Governance rules can branch by intent origin.

## Phase 4: Inter-origin semantic conflict detection (5-8 days)

Scope: conflicts **between** intents from different origins/plans targeting the same branch. Intra-plan coherence is the generator's responsibility — Converge is the only point with visibility of all intents in flight from all origins.

1. Candidate generation
   - Use embedding similarity to shortlist potentially conflicting intents.
   - Compare only intents from **different** plan_id values (or null plan_id) targeting the same branch.
   - Same-plan intents are excluded (generator already validated coherence).
2. Conflict heuristics (v1)
   - Intent overlap + opposing target signals + branch/area coupling.
3. Resolution workflow
   - New events:
   - `intent.semantic_conflict.detected`, `intent.semantic_conflict.resolved`.
   - Shadow mode: detect and log, auto-resolve (no blocking).
   - Enforced mode: block intent at validation.
4. API and dashboard
   - Expose open inter-origin conflicts and confidence scores.
5. Tests
   - False-positive control scenarios and explainability assertions.
   - Verify same-plan intents are never flagged against each other.

Definition of done:
- Inter-origin semantic conflicts are surfaced with explanation and confidence.
- Intra-plan pairs are excluded from detection.
- Shadow mode stable before any enforcement.

## Phase 5: Verification debt end-to-end (3-5 days)
1. Add projection module
   - `src/converge/projections/verification.py`
2. Add API + CLI
   - Endpoint: `/api/verification/debt`
   - CLI: `converge verification debt`
3. Add compliance integration
   - Tenant threshold + alert when debt exceeds limit.
4. Add tests
   - Unit tests for score math.
   - API tests for endpoint and threshold behavior.

Definition of done:
- Debt score visible per tenant.
- Compliance emits debt alerts.
- Projection tests >90% coverage.

## Phase 6: Human review orchestration (7-10 days)
1. Data model and events
   - Entity: `review_task`.
   - Events: `review.requested`, `review.assigned`, `review.escalated`, `review.completed`.
2. Assignment and SLA rules
   - Rules based on severity, risk, queue age, and semantic conflict confidence.
   - Triggered by policy evaluation or conflict detection results.
3. API + CLI operations
   - Create/assign/reassign/complete/escalate review tasks.
4. Dashboard views
   - Aging, SLA breaches, task distribution.

Definition of done:
- Critical blocked or semantically conflicted intents can trigger human review.
- SLA breaches generate events.
- Aging and ownership are queryable.

## Phase 7: Native security adapters (7-10 days)
1. Add scanner port in `src/converge/ports.py`.
2. Add adapters:
   - `bandit` (SAST), `pip-audit` (SCA), `gitleaks` (secrets), plus legacy shell fallback.
3. Normalize findings
   - Common severities, evidence format, and decision hooks.
4. Policy integration
   - Enforce/soft-enforce by severity thresholds.

Definition of done:
- Security findings are normalized and evented.
- Policy can block on configured severity.
- Legacy fallback remains compatible.

## Phase 8: Adaptive intake/backpressure (3-5 days)
1. Intake pre-check in policy evaluation (before intent enters pipeline).
2. Modes:
   - `open`, `throttle`, `pause-critical-only`.
3. Eventing + telemetry:
   - `intake.throttled`, `intake.paused`.
4. Inputs:
   - verification debt + semantic conflict pressure + queue health.

Definition of done:
- System can reduce intake under verification stress.
- Decisions are auditable by tenant and reason.

## Phase 9: Hardening and rollout (5-7 days)
1. Load and soak tests for queue, dashboard, new endpoints.
2. Feature flags per capability:
   - `intent_links`, `intent_semantics`, `origin_policy`, `semantic_conflicts`,
   - `verification_debt`, `review_tasks`, `security_adapters`, `intake_control`, `plan_coordination`.
3. Runbook updates and incident playbooks.

Definition of done:
- Staged rollout in dev/staging/prod.
- No readiness regressions.
- Operational docs updated.

## Risks and mitigations
1. Engine complexity growth
   - Mitigation: isolate new logic in projections/services/adapters.
2. Excessive security false positives
   - Mitigation: shadow mode then gradual enforcement.
3. Adoption friction for review tasks
   - Mitigation: start with critical-only automation and simple SLA.
4. Embedding model drift
   - Mitigation: semantic versioning, reindex command, shadow evaluation before switching.
5. Inter-origin conflict false positives
   - Mitigation: start shadow-only, exclude intra-plan pairs, require confidence + multi-signal checks.
6. Dependency cycles in plans
   - Mitigation: detect and reject at intent creation time.

## Timeline
1. Semantic foundation (Phases 0-4): 3-5 weeks.
2. Full enterprise closure (Phases 0-9): 7-9 weeks.

## Recommended execution order

### Foundation
1. Phase 0 (all ADRs including ADR-006 plan coordination)
2. Phase 1 (Intent -> PR -> Commit explicit model + plan_id)
3. Phase 2 (Embeddings for semantic intents)
4. Phase 3 (Human/agent native distinction)

### Conflict and governance
5. Phase 4 (Semantic conflict detector, shadow mode)
6. Phase 6 (Human review orchestration)
7. Phase 8 (Adaptive intake/backpressure)

### Security and compliance
8. Phase 7 (Native security adapters)

### Transversal
9. Phase 5 (Verification debt — feeds policy and intake)

### Hardening
10. Phase 9 (hardening/rollout)
