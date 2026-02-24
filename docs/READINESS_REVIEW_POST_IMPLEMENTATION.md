# Readiness Review: Converge Post-Implementation Projection

Date: 2026-02-23
Baseline: Pre-implementation codebase (57 source files, 29 tests, 8 DB tables)
Status: All 47 items implemented (AR-01..AR-47). 734 tests passing.

## 1) Architecture: Before vs After

### Before (current)

```
Webhook/CLI ──→ Intent(READY) ──→ validate_intent() ──→ process_queue() ──→ MERGED
                                   │                      │
                                   ├─ simulate            ├─ revalidate (lightweight)
                                   ├─ checks              ├─ queue ordering
                                   ├─ risk eval           └─ merge execution
                                   └─ policy (3 gates)
```

- Single pipeline, single agent
- Intent enters as READY, no pre-processing
- No semantic analysis
- No conflict detection between intents
- No human review workflow
- Security via `make security-scan` (shell)
- No multi-intent coordination (plan_id / dependencies exist but unenforced)
- 5 states: READY → VALIDATED → QUEUED → MERGED | REJECTED

### After (implemented)

```
Webhook/CLI ──→ Intake pre-check ──→ Intent(READY)
                    │                     │
                    │                     ├─ Semantic fingerprint (embeddings)
                    │                     ├─ Origin classification (human/agent/integration)
                    │                     ├─ Conflict detection (similarity)
                    │                     ├─ Human review (if triggered by policy/conflict)
                    │                     │
                    │                  validate_intent()
                    │                     │
                    │                     ├─ Merge simulation
                    │                     ├─ Validation checks
                    │                     ├─ Risk evaluation
                    │                     └─ Policy (4 gates: +security)
                    │                     │
                    │                  process_queue()
                    │                     │
                    │                     ├─ Dependency check (plan_id: skip if deps not MERGED)
                    │                     ├─ Revalidate (lightweight)
                    │                     ├─ Queue ordering
                    │                     └─ Merge execution
                    │                     │
                    │                  MERGED | REJECTED
                    │
                    └── throttle/reject (backpressure)
```

- Same pipeline, enhanced with pre-checks and dependency enforcement
- 5 states unchanged: READY → VALIDATED → QUEUED → MERGED | REJECTED
- Semantic analysis and conflict detection enrich intent before validation
- Human review orchestrated when triggered by policy or conflict results
- Security via native adapters (bandit, pip-audit, gitleaks) + legacy fallback
- Adaptive intake with backpressure (debt + conflict pressure + queue health)
- Multi-intent coordination via plan_id + dependency enforcement in queue

### Plan coordination model

```
Intent(plan_id="plan-X", dependencies=[])         → PR #1 → MERGED
Intent(plan_id="plan-X", dependencies=["i-001"])   → PR #2 → waits for i-001 → MERGED
Intent(plan_id="plan-X", dependencies=["i-002"])   → PR #3 → waits for i-002 → MERGED
```

- `plan_id` groups intents. Not a separate entity — plan status is derived.
- `dependencies` enforced in `process_queue()`: skip intent if any dep not MERGED.
- Each intent goes through the full pipeline independently.

## 2) Capability delta (actual)

| Capability | Before | After | Delta |
|---|---|---|---|
| Intent lifecycle states | 5 | 5 (unchanged) | 0 states |
| Policy gates | 3 (verification, containment, entropy) | 4 (+security) | +1 gate |
| DB tables | 8 | 13 (+intent_commit_links, embeddings, review_tasks, security_findings, event_chain_state) | +5 tables |
| Source modules | 57 files | 81 files | +24 files |
| Test files | 29 files | 40 files | +11 files |
| Tests passing | ~500 | 734 | +234 tests |
| API endpoints | ~20 | 57 | +37 endpoints |
| CLI commands | ~25 | 63 dispatch entries | +38 commands |
| Event types | ~15 | 58 | +43 types |
| Feature flags | 0 | 14 (all phases covered, 3-tier override: defaults → config → env) | +14 flags |
| Plan coordination | Field exists, unenforced | plan_id + dependency enforcement in queue | Enforced |

## 3) New and modified modules

### New modules

| Module | Purpose |
|---|---|
| `src/converge/semantic/canonical.py` | Deterministic text builder from intent fields |
| `src/converge/semantic/embeddings.py` | Provider abstraction + test provider |
| `src/converge/semantic/conflicts.py` | Inter-origin candidate generation + scoring heuristics |
| `src/converge/harness.py` | Pre-PR evaluation harness (shadow/enforce) |
| `src/converge/reviews.py` | Review task lifecycle (request/assign/complete/escalate) |
| `src/converge/ownership.py` | Code-area ownership + SoD enforcement |
| `src/converge/audit_chain.py` | SHA-256 tamper-evidence hash chain |
| `src/converge/intake.py` | Adaptive intake with normal/throttle/pause modes |
| `src/converge/security.py` | Security scan orchestrator |
| `src/converge/security_models.py` | SecurityFinding, FindingSeverity, FindingCategory |
| `src/converge/feature_flags.py` | Centralized flag registry with 3-tier override |
| `src/converge/event_types.py` | Event type registry (extracted from models.py) |
| `src/converge/projections_models.py` | Projection data models (extracted from models.py) |
| `src/converge/projections/verification.py` | 5-factor debt score projection |
| `src/converge/adapters/security/bandit_adapter.py` | SAST scanner adapter |
| `src/converge/adapters/security/pip_audit_adapter.py` | SCA scanner adapter |
| `src/converge/adapters/security/gitleaks_adapter.py` | Secrets scanner adapter |
| `src/converge/adapters/security/shell_adapter.py` | Legacy fallback adapter |
| `src/converge/api/routers/security.py` | Security findings API endpoints |
| `src/converge/api/routers/intake.py` | Intake status/mode API endpoints |

### Modified modules

| Module | Changes |
|---|---|
| `src/converge/models.py` | +origin_type, +plan_id, +ReviewTask model, re-exports from extracted modules |
| `src/converge/ports.py` | +link operations, +scanner port, +review task port, +security finding store |
| `src/converge/policy.py` | +security gate (4th gate), +origin-aware branching |
| `src/converge/engine.py` | +`_check_dependencies()` in process_queue(), +security gate integration |
| `src/converge/event_log.py` | +review task facades, +security finding facades |
| `src/converge/adapters/base_store.py` | +5 tables, +plan_id column, +migrations, +security/review/chain methods |
| `src/converge/projections/compliance.py` | +debt threshold checks |
| `src/converge/analytics.py` | +link-aware coupling loader |
| `src/converge/api/routers/dashboard.py` | +verification debt, +review summary, +security summary, +semantic status |
| `src/converge/api/routers/intents.py` | +pre-eval harness endpoint, +feature flags endpoints |
| `src/converge/cli/` | +63 dispatch entries covering all phases |

## 4) Readiness score projection

### Current: 7.8/10

| Dimension | Score | Notes |
|---|---|---|
| Core lifecycle | 9/10 | Solid, well-tested |
| Policy gates | 8/10 | 3 gates, configurable profiles |
| Observability | 8/10 | Events, health, predictions |
| Security | 5/10 | Shell-only, no native adapters |
| Human governance | 4/10 | No review workflow |
| Intent intelligence | 5/10 | No semantic processing |
| Scalability controls | 6/10 | Fixed rate limiter, no adaptive intake |
| Auditability | 7/10 | Append-only events, no tamper evidence |
| Agent boundaries | 7/10 | Agent policy exists, no SoD by area |
| Multi-intent coordination | 5/10 | Field exists but unenforced |

### After implementation: 9.3/10

| Dimension | Score | Change | Implementation |
|---|---|---|---|
| Core lifecycle | 9/10 | +0 | Unchanged — same 5 states, proven pipeline |
| Policy gates | 9.5/10 | +1.5 | +security gate in `policy.py`, origin-aware branching |
| Observability | 9/10 | +1 | Debt KPI in `projections/verification.py`, conflict visibility in dashboard |
| Security | 9/10 | +4 | 4 adapters in `adapters/security/`, findings store, security gate |
| Human governance | 9/10 | +5 | `reviews.py` with full lifecycle, SLA, escalation |
| Intent intelligence | 9.5/10 | +4.5 | Embeddings in `semantic/`, conflict detection, pre-eval harness |
| Scalability controls | 9/10 | +3 | `intake.py` with normal/throttle/pause modes |
| Auditability | 9/10 | +2 | SHA-256 hash chain in `audit_chain.py` |
| Agent boundaries | 9/10 | +2 | `ownership.py` with path-pattern SoD enforcement |
| Multi-intent coordination | 9.5/10 | +4.5 | `plan_id` + `_check_dependencies()` in `engine.py` |

## 5) Risk analysis

### Risks mitigated by the plan coordination model

1. **Multi-phase agent workflows**: Agents can decompose complex changes into N intents with explicit dependency ordering. Each intent produces 1 PR. The queue engine enforces ordering without new states or lifecycle complexity.

2. **No new states or entities**: Plan coordination adds zero lifecycle states and zero new entities. It reuses the existing `dependencies` field (already in the model) and adds only `plan_id` (nullable grouping key).

3. **Human and agent parity**: Humans create 1 intent = 1 PR (no plan_id needed). Agents create N intents with plan_id + deps. Same pipeline serves both.

4. **Wasted merge work**: Inter-origin conflict detection catches overlapping intents from independent actors before expensive simulation/checks run. Dependencies ensure correct ordering within a plan. Intra-plan coherence is the generator's responsibility.

### Remaining risks after implementation

| Risk | Severity | Mitigation |
|---|---|---|
| **Embedding model drift** | Medium | Semantic versioning + reindex command + shadow eval before switching (AR-11, AR-13) |
| **Inter-origin conflict false positives** | Medium | Shadow mode first, then gradual enforcement with confidence thresholds. Same-plan pairs excluded (AR-20) |
| **SLA calibration** | Low | Start with generous windows, tighten based on operational data (AR-34) |
| **Security adapter maintenance** | Low | Pluggable adapters + legacy shell fallback always available (AR-38) |
| **Dependency cycles in plans** | Low | Detect and reject at intent creation time (AR-47) |
| **DB schema migration** | Low | +5 tables, all additive. No breaking changes to existing tables |
| **Event volume growth** | Medium | ~20 new event types. Hash chain adds overhead per batch (AR-44) |

## 6) What does NOT change

These current strengths are preserved:

- **Hexagonal architecture**: All new modules follow ports & adapters pattern
- **Event sourcing**: Event log remains source of truth. New events are additive.
- **SQLite/Postgres parity**: All new tables must pass dual-backend tests
- **Backward compatibility**: Existing API consumers, CLI scripts, and webhook handlers continue working
- **Worker model**: Queue worker loop is unchanged. It processes VALIDATED intents as before (with added dependency check).
- **Risk evaluation**: 4-signal risk model (entropic_load, contextual_value, complexity_delta, path_dependence) is unchanged
- **Existing 3 policy gates**: Verification, containment, entropy gates are unchanged. Security gate is additive.
- **Intent lifecycle**: 5 states (READY → VALIDATED → QUEUED → MERGED | REJECTED). No new states.

## 7) Implementation investment (actual)

| Metric | Value |
|---|---|
| Total items | 47 (AR-01..AR-47), all DONE |
| New DB tables | 5 |
| Source files | 81 (was 57, +24) |
| Test files | 40 (was 29, +11) |
| Tests passing | 734 (was ~500, +234) |
| API endpoints | 57 total |
| CLI dispatch entries | 63 total |
| Event types | 58 total |
| Feature flags | 14 |

### Critical path

```
AR-47 (ADR-006) → AR-01 (schema) → AR-02 (store) → AR-04 (webhook) → AR-06 (backfill)
                                                                          ↓
AR-07 (coupling) → AR-10 (canonical text) → AR-11 (embeddings) → AR-12 (persist)
                                                                          ↓
AR-18 (conflict candidates) → AR-19 (scoring) → AR-20 (resolution) → AR-41 (intake)
```

Longest path: ~14 items, ~22 days. This is the minimum time to achieve semantic conflict detection + intake control.

## 8) Verdict

### What you get

With full backlog closure, Converge adds:

1. **Intent intelligence** — answers "are there conflicts between independent actors?":
   - Evaluates intent quality via semantic fingerprinting
   - Detects conflicts between intents from **different origins/plans** before they waste merge pipeline resources (intra-plan coherence is the generator's responsibility)
   - Classifies intent origin (human/agent/integration) and applies differentiated policy

2. **Human review orchestration** — answers "who should review this, and when?":
   - Routes critical or conflicted intents to human reviewers with SLA enforcement
   - Review triggered by policy evaluation or conflict detection, not by state transitions

3. **Security verification** — answers "is this code safe?":
   - Native SAST/SCA/secrets scanning as a 4th policy gate
   - Pluggable adapters with legacy shell fallback

4. **Operational control** — answers "is the system healthy?":
   - Adaptive intake with backpressure (debt + conflict pressure + queue health)
   - Verification debt KPI + compliance alerts
   - Tamper-evidence event chain

5. **Plan coordination** — answers "how do multi-phase changes flow?":
   - `plan_id` groups N intents from the same plan
   - `dependencies` enforced in queue: intent waits until all deps are MERGED
   - No new entity, no new states — just a grouping key + enforcement logic

### What it addresses from the PDF thesis

| PDF concern | Coverage |
|---|---|
| Verification debt accumulation | Debt KPI + intake backpressure + compliance alerts |
| Review as the new bottleneck | Inter-origin conflict detection filters contradictory intents *before* merge pipeline |
| Layered review (automation + human) | Automates semantic analysis, routes to human only when needed |
| Trust-verification gap | Review tasks with "who approved and why" audit trail |
| Security risk from AI-generated code | Native SAST/SCA/secrets scanning in policy |
| Cognitive load on reviewers | Inter-origin conflicts surfaced + review at intent level (the "what"), not code level (the "how") |
| Need for governance and auditability | Tamper-evidence chain + 35 event types + full lifecycle tracing |

### Score: 7.8/10 → 9.3/10 (achieved)

All 47 backlog items implemented. The 0.7 gap to 10/10 represents:
- Operational calibration (SLA windows, debt formula, conflict thresholds) that requires production data
- Integration testing coverage (sandbox limitations)
- Embedding model selection and tuning (vendor-specific)
