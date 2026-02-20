# Converge

Code entropy control through semantic merge coordination.

Converge manages **intents** — semantic contracts representing proposed changes — through a controlled lifecycle before they merge into your codebase. Every decision is recorded as an immutable event, giving you full audit trail, health monitoring, and predictive capabilities.

## Install

```bash
pip install -e .
```

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
- Simulates the merge in an isolated git worktree
- Evaluates risk (entropy, damage, propagation, containment)
- Checks the 3 policy gates (verification, containment, entropy)
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
converge intent   {create, list, status}     Intent lifecycle
converge simulate                             Merge simulation (worktree)
converge validate                             Full pipeline: sim + risk + policy
converge merge    {confirm}                   Confirm merge
converge queue    {run, reset, inspect}       Queue operations
converge policy   {eval, calibrate}           Policy evaluation
converge risk     {eval, shadow, gate,        Risk scoring and analysis
                   review, policy-set,
                   policy-get}
converge health   {now, trend, change,        Health monitoring
                   change-trend, entropy}
converge compliance {report, alerts,          SLO/KPI compliance
                     threshold-set,
                     threshold-get,
                     threshold-list}
converge agent    {policy-set, policy-get,    Agent authorization
                   policy-list, authorize}
converge audit    {prune, events}             Event log queries
converge metrics                              Integration metrics
converge archaeology                          Git history analysis
converge predictions                          Predictive signals
converge serve                                HTTP API server
```

## Policy gates

Three gates evaluated for every intent:

| Gate | What it checks | Threshold varies by |
|---|---|---|
| **Verification** | Required checks passed (lint, tests) | risk_level |
| **Containment** | Change is scoped, not spreading everywhere | risk_level |
| **Entropy** | Change complexity within budget | risk_level |

Risk profiles (`low`, `medium`, `high`, `critical`) define the thresholds. Customize in `policy.json` or calibrate from history:

```bash
converge policy calibrate
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

All read endpoints from projections, all writes produce events. Auth via `CONVERGE_API_KEYS` env var with RBAC (`viewer`, `operator`, `admin`).

Key endpoints:
- `GET /health`
- `GET /api/summary`
- `GET /api/intents`
- `GET /api/health/repo/now`
- `GET /api/compliance/report`
- `GET /api/risk/review?intent_id=X`
- `GET /api/predictions`
- `GET /api/events`
- `POST /integrations/github/webhook` (pull_request, push, merge_group)

## Environment variables

| Variable | Purpose |
|---|---|
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
