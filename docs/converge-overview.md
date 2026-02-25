# Converge Overview

Converge is an intent-driven integration control system for repositories with frequent parallel change.
It decides whether a change should advance based on mergeability, required checks, risk, policy gates, and system coherence.

## Why It Exists

Passing CI is necessary but not sufficient for safe integration.
Converge addresses integration risk that appears at the system level:

- many individually valid changes interacting badly
- growing structural entropy across modules
- hidden blast radius from highly connected files
- stale validations when target branch already moved

## Core Model

Converge manages `Intent` entities (change contracts) with lifecycle states:

`READY -> VALIDATED -> QUEUED -> MERGED` (or `REJECTED`)

Every state transition emits immutable events. Projections (health, trends, compliance, metrics) are derived from event history.

## Validation Pipeline

For each intent, Converge evaluates:

1. Merge simulation (`git merge-tree`) against current target state
2. Required checks based on risk profile (for example lint, unit tests, security scan)
3. Risk evaluation
4. Coherence harness evaluation
5. Policy gates
6. Optional semantic conflict detection (feature-flag controlled)
7. Decision event with evidence (`allowed` or `blocked`)

## Three Invariants

1. **Mergeability + checks**: an intent advances only if it can merge and required checks pass.
2. **Revalidation on moving target**: queued work is revalidated against the latest target state.
3. **Retry bound**: repeated failures are rejected after configured max retries.

These invariants are the load-bearing behavior of the engine.

## Policy Gates

Converge evaluates independent gates and combines them with blocking logic:

- Verification
- Containment
- Entropy
- Coherence
- Security

Thresholds are configurable per risk level (`low`, `medium`, `high`, `critical`) and calibratable from repository history.

## Risk and Entropy Control

Risk evaluation includes multiple structural signals (for example entropy delta, containment, propagation, impact context).
Resulting scores are used both for visibility and policy enforcement.

Risk auto-classification can be enabled via feature flags.

## Coherence Harness

Coherence is a configurable set of system-level checks (question + command + assertion + severity).
It is intended to catch regression of repository invariants that are not always visible in per-change checks.

The harness supports:

- baseline comparison
- weighted scoring with pass/warn/fail thresholds
- suggestion/feedback loop (feature-flag controlled)

## Semantics and LLMs

Converge supports semantic indexing/conflict workflows, but provider quality depends on configuration:

- default embedding provider is deterministic (stable/reproducible, weak semantic understanding)
- optional `sentence-transformers` provider enables stronger semantic similarity
- semantic conflict enforcement can run in `shadow` or `enforce` mode

LLM review advisor is optional and disabled by default. The merge/reject core path remains deterministic.

## Merge Execution and Isolation

- Merge simulation uses `git merge-tree` (no checkout, no worktree mutation).
- Real merge execution can run through isolated worktree flow (`execute_merge_safe`) to avoid polluting the main working directory.

## Storage, Concurrency, and Deployment

- Event log is the source of truth.
- SQLite is the default backend.
- Queue coordination uses lock primitives in store adapters.
- PostgreSQL advisory locks are supported behind feature flags (`shadow`/`enforce`).

This is designed first for single-repo operational robustness, with optional hardening paths.

## What Converge Is

- An intent-centered merge coordination and decision engine
- An event-sourced audit trail for integration decisions
- A configurable policy/risk/coherence enforcement layer
- A CLI/API/worker system that can be integrated with GitHub workflows

## What Converge Is Not

- Not a replacement for CI quality
- Not a full semantic code-understanding engine by default configuration
- Not a silver bullet for architecture quality without explicit repository invariants

## Current Reality (Important)

Converge is production-oriented and test-heavy, but behavior depends on configuration choices:

- semantic quality depends on embedding provider
- some advanced capabilities are feature-flag gated
- policy strictness depends on profile thresholds

The system promise is strongest when policy, coherence, and semantic providers are explicitly configured for the target repository.

## Operational Reality (Practical)

- Command surface is intentionally tiered: essential workflow commands are shown in default help, while the full surface (risk/health/compliance/semantic/review/security/export/coherence) is available in full help.
- Several capabilities are effective only with external tooling installed:
  - security scanning adapters require their scanner binaries/toolchains
  - stronger semantic similarity requires an ML embedding provider (for example `sentence-transformers`)
  - LLM advisory requires provider credentials and SDKs
- Queue processing is sequential by design to preserve revalidation guarantees after each integration.
- The strongest value appears when teams treat Converge as policy infrastructure (profiles + harness + review workflow), not only as a merge command wrapper.

## Known Boundaries

- Deterministic default semantics are reproducible but not deep semantic understanding.
- Coherence quality depends on question quality and maintenance.
- Outbound notifications are feature-flagged and should be treated as optional integration, not core decision reliability.
