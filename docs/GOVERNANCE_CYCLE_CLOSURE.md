# Cycle Closure — Final Stage Governance

This document formalizes the 4 requirements to close the loop between
vision, design, operation, and learning.

**Current status: none of the 4 requirements is implemented as a formal process.**
The technical infrastructure to support them exists (metrics, compliance, audit chain),
but there are no operational processes or evidence of execution.

## 1. Numeric success criteria per stage

Define and publish quantitative objectives before executing each stage:

- `SLO_validation_p95_ms`
- `SLO_queue_throughput_intents_hour`
- `SLO_api_error_rate`
- `SLO_api_latency_p95_ms` / `SLO_api_latency_p99_ms`
- `SLO_requeue_rate_max`
- `SLO_entropy_drift_max_weekly`

Governance rule:
- A stage is not declared closed without evidence of SLO/SLA compliance.

**Status:** The infrastructure to measure SLOs exists (`converge compliance report`,
`converge health now`, API `/metrics`). Thresholds are configurable via
`converge compliance threshold-set`. There is no evidence of published SLOs or
formal compliance reviews.

## 2. Real adoption plan (pilot teams)

Each product stage must validate real usage with concrete teams:

- Pilot team A (primary repository)
- Pilot team B (repository with different dynamics)
- Minimum pilot duration: 2-4 weeks
- Mandatory feedback: friction points, perceived value, false positives, timings

Governance rule:
- New features are not prioritized without learnings from the previous pilot.

**Status:** No pilot program is running. The smoke tests (Phase 1 + Phase 2)
validated that the system works end-to-end, but they are not real adoption with
a team using the system in their daily workflow.

## 3. Compatibility and migration policy

Define an explicit policy for:

- Database schema versioning
- Event type (`event_type`) and payload compatibility
- External and internal API versioning
- Per-version rollback strategy

Governance rule:
- Every incompatible change must include a migration, rollback plan, and parity test.

**Status:** Postgres/SQLite rollback is documented in the RUNBOOK.
The schema auto-migrates via `ensure_db()`. There is no formal versioning
of event payloads or API compatibility policy.

## 4. Architecture review cadence

Establish a periodic structural review:

- Frequency: monthly
- Minimum inputs:
  - Complexity per module
  - Coupling between layers
  - Entropy drift
  - Post-merge incidents
  - Open/closed technical debt
- Output:
  - Decisions (short ADRs)
  - Prioritized refactoring plan

Governance rule:
- If a review detects sustained degradation, functional expansion is frozen
  until structural indicators are restored.

**Status:** No review cadence is established. The data to feed
the review exists (`converge health trend`, `converge verification debt`,
`converge compliance report`), but there is no recurring process or ADRs.

## Definition of cycle closure

The cycle is considered closed when:

1. There are active and auditable numeric metrics.
2. There is real pilot adoption with evidence of usage.
3. There is a formal compatibility/migration policy in effect.
4. There is a recurring architecture review with recorded decisions.

**Conclusion: the cycle is not closed.** The 4 requirements are prerequisites
to consider Converge ready for formal production. The technical infrastructure
is ready; the operational processes are missing.
