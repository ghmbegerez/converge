# Converge

Code entropy control through semantic merge coordination.

Converge manages **intents** — semantic contracts representing proposed changes — through a controlled lifecycle before they merge into your codebase. Every decision is recorded as an immutable event, giving you full audit trail, health monitoring, and predictive capabilities.

## Why Converge

The core problem is a **rate mismatch**: humans and agents can create candidate changes faster than a repository can safely integrate them.
Converge regulates that gap with explicit validation, risk, and policy gates so integration throughput is driven by evidence, not by arrival rate.

## Install

```bash
pip install -e .
```

## Prerequisites and assumptions

Hard prerequisites (required for core flow):
- Run inside a git repository.
- Source/target refs used by intents must exist and be reachable by git.
- Writable state database (`.converge/state.db` by default, or `--db` / env override).
- Python environment with project dependencies installed.

Operational assumptions (core behavior depends on these):
- Required checks (`lint`, `unit_tests`, etc.) assume corresponding commands/tooling exist in the repo environment.
- Queue safety assumes single active queue processor per backend lock domain, unless you explicitly configure distributed locking strategy.
- Validation decisions are deterministic and policy-driven; blocked intents are expected outcomes, not exceptions.

Intent/plan prerequisites (before processing):
- Each intent must have a valid `source`, `target`, and lifecycle start state (normally `READY`).
- Queue processing advances intents from `VALIDATED`; intents in `READY` are expected to be validated first.
- If an intent declares `dependencies`, each dependency intent is expected to reach `MERGED` before processing; otherwise the intent is `dependency_blocked`.
- `plan_id` groups related intents, but execution order is defined by dependency edges, not by creation timestamp.
- Dependency graphs are expected to be acyclic in practice; circular dependencies can stall progression until manually resolved.
- Starting point assumption for a plan: referenced dependency intent IDs already exist in the same event store scope/tenant.

Optional capabilities (only if configured):
- Strong semantic similarity requires a non-deterministic embedding provider (for example sentence-transformers).
- Security gate quality depends on scanner toolchain availability.
- LLM advisory requires provider credentials and SDKs.

## Quick start (5 minutes)

### 1. Create an intent

```json
{
  "intent_id": "intent-001",
  "source": "feature/login",
  "target": "main",
  "status": "READY",
  "risk_level": "medium",
  "priority": 2,
  "semantic": {
    "problem_statement": "Users cannot authenticate",
    "objective": "Add login capability"
  },
  "technical": {
    "source_ref": "feature/login",
    "target_ref": "main",
    "initial_base_commit": "abc123",
    "scope_hint": ["auth"]
  }
}
```

```bash
converge intent create --file intent.json
```

### 2. Validate (simulate + risk + policy)

```bash
converge validate --intent-id intent-001
```

This runs the full pipeline:
- Simulates the merge via `git merge-tree`
- Evaluates risk (entropy, damage, propagation, containment)
- Checks the 5 policy gates (verification, containment, entropy, coherence, security)
- Returns `validated` or `blocked` with evidence

### 3. Process the queue

```bash
converge queue run --auto-confirm
```

Processes all VALIDATED intents by priority. For each one:
- Revalidates against the current state of main (invariant 2)
- If blocked, increments retries; after max retries, rejects (invariant 3)
- If `--auto-confirm`, executes the real git merge

### 4. Check health

```bash
converge health now
```

```json
{
  "repo_health_score": 98.0,
  "entropy_score": 4.0,
  "mergeable_rate": 1.0,
  "status": "green",
  "learning": {
    "summary": "Repo health is strong (score: 98)",
    "lessons": []
  }
}
```

## Core invariants

The entire system implements three invariants:

```
1. mergeable(i, t) = can_merge(M(t), Δi) ∧ checks_pass
2. If M(t) advances → revalidate
3. retries > N → reject
```

Everything else — risk scoring, health monitoring, compliance, predictions — is derived from events produced by these invariants.

## Architecture

```
ENGINE (hot path)                    → produces Events
  simulate → check → policy → decide

EVENT LOG (source of truth)          → append-only, immutable
  Every decision is an event with trace_id + evidence

PROJECTIONS (derived views)          → rebuilt from events
  health, compliance, risk trends, predictions, learning

POLICY (dynamic constraints)         → informed by projections
  profiles, risk gates, agent authorization
```

## Commands

```
converge --help                               Essential workflow commands
converge --help-all                           Full command surface

Core workflow:
  converge intent   {create, list, status}
  converge simulate
  converge validate
  converge queue    {run, reset, inspect}
  converge merge    {confirm}

Operational/advanced:
  converge doctor
  converge risk / health / compliance / verification / predictions
  converge semantic / coherence / harness / review / security / export
  converge agent / audit / metrics / archaeology
  converge serve / worker
```

## Policy gates

Five gates evaluated for every intent:

| Gate | What it checks | Threshold varies by |
|---|---|---|
| **Verification** | Required checks passed (lint, tests) | risk_level |
| **Containment** | Change is scoped, not spreading everywhere | risk_level |
| **Entropy** | Change complexity within budget | risk_level |
| **Coherence** | System-level invariants via harness score | risk_level |
| **Security** | Security findings against severity thresholds | risk_level |

Risk profiles (`low`, `medium`, `high`, `critical`) define the thresholds. Customize in `policy.json` or calibrate from history:

```bash
converge policy calibrate
```

## Semantics and feature flags

- Semantic conflict workflows exist in the CLI/API, but semantic quality depends on embedding provider configuration.
- Default embedding mode is deterministic; for stronger semantic similarity, configure a model provider (for example sentence-transformers).
- Advanced behavior is controlled by feature flags (for example semantic conflicts, coherence feedback, advisory locks, LLM review advisor, notifications).
- Inspect and tune with:

```bash
converge --help-all
converge doctor
```

## Risk evaluation

```bash
converge risk eval --intent-id intent-001
```

Computes:
- **risk_score**: combined score (0-100)
- **damage_score**: potential damage based on files, conflicts, target branch
- **entropy_score**: change complexity (files touched, dependencies, conflicts)
- **propagation_score**: blast radius from impact graph
- **containment_score**: how well-scoped the change is (0-1)
- **findings**: specific issues (large change, core target, conflicts, dependency spread)
- **impact_edges**: directed graph of what the change affects

## Agent authorization

Agents (CI bots, AI assistants) need authorization to act:

```bash
# Configure agent policy
converge agent policy-set --agent-id bot-1 \
  --atl 2 \
  --allow-actions analyze,merge \
  --max-risk-score 50 \
  --require-human-approval false

# Check if agent can merge
converge agent authorize --agent-id bot-1 --action merge --intent-id intent-001
```

Checks: allowed actions, risk limits, blast severity, compliance, human approvals, expiration.

## Health and predictions

```bash
converge health now                    # Current repo health score
converge health trend --days 30        # Health over time
converge health entropy --days 30      # Entropy trend
converge compliance report             # SLO/KPI checks
converge compliance alerts             # Active alerts
converge predictions                   # Predicted issues
```

Predictions detect: rising conflict rates, entropy spikes, queue stalling, high rejection rates.

## Audit trail

Every action produces an immutable event:

```bash
converge audit events --limit 20
converge audit events --type risk.evaluated --intent-id intent-001
converge audit prune --retention-days 90 --dry-run
```

Event types: `intent.created`, `simulation.completed`, `check.completed`, `risk.evaluated`, `policy.evaluated`, `intent.validated`, `intent.merged`, `intent.rejected`, `intent.blocked`, `agent.authorized`, `health.snapshot`, `queue.processed`, and more.

## HTTP API

```bash
converge serve --port 9876
```

Converge exposes:
- **Canonical API prefix**: `/v1`
- **Compatibility prefix**: `/api` (legacy mirror)
- **Operational endpoints outside version prefix**: `/health`, `/health/ready`, `/health/live`, `/metrics`
- **Webhook endpoint**: `/integrations/github/webhook`

Auth/RBAC is controlled via `CONVERGE_API_KEYS` (`viewer`, `operator`, `admin`) when auth is enabled.

Representative endpoints:
- `GET /v1/intents`
- `GET /v1/summary`
- `GET /v1/risk/review?intent_id=<id>`
- `GET /v1/health/repo/now`
- `GET /v1/events`
- `GET /v1/compliance/report`
- `GET /v1/predictions`
- `POST /integrations/github/webhook`

For the exact live contract in your running build, use the OpenAPI docs served by FastAPI (`/docs`).

## Environment variables

| Variable | Purpose |
|---|---|
| `CONVERGE_DB_PATH` | SQLite database path (default: `.converge/state.db`) |
| `CONVERGE_DB_BACKEND` | Storage backend: `sqlite` or `postgres` (default: `sqlite`) |
| `CONVERGE_TRACE_ID` | Fixed trace ID for a run |
| `CONVERGE_AUTH_REQUIRED` | Enable API auth (default: `1`) |
| `CONVERGE_API_KEYS` | API key registry (`key:role:actor:tenant`) |
| `CONVERGE_RATE_LIMIT_ENABLED` | Enable per-tenant rate limiting (default: `1`) |
| `CONVERGE_RATE_LIMIT_RPM` | Requests per minute per tenant (default: `120`) |
| `CONVERGE_GITHUB_APP_ID` | GitHub App numeric ID (enables GitHub integration) |
| `CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH` | Path to PEM private key file |
| `CONVERGE_GITHUB_APP_PRIVATE_KEY` | PEM contents (fallback, not recommended for prod) |
| `CONVERGE_GITHUB_INSTALLATION_ID` | GitHub App installation ID (global default) |
| `CONVERGE_GITHUB_WEBHOOK_SECRET` | GitHub webhook HMAC secret |
| `CONVERGE_GITHUB_DEFAULT_TENANT` | Default tenant for PR intents |
| `CONVERGE_WORKER_POLL_INTERVAL` | Worker poll interval in seconds (default: `5`) |
| `CONVERGE_WORKER_BATCH_SIZE` | Max intents per worker cycle (default: `20`) |
| `CONVERGE_WORKER_TARGET` | Target branch for queue processing (default: `main`) |
| `CONVERGE_WORKER_AUTO_CONFIRM` | Auto-confirm merges: `1` = yes (default: `0`) |

## State

All state lives in a single SQLite database (default: `.converge/state.db`).

The `events` table is the source of truth. The `intents` table is a materialized view. If projections corrupt, they can be rebuilt from events.
