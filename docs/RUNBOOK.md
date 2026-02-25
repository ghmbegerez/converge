# Converge Operations Runbook

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│  GitHub App  │────>│  Converge    │────>│ PostgreSQL │
│  (webhooks)  │     │  API Server  │     │  16-alpine │
└─────────────┘     └──────┬───────┘     └─────┬──────┘
                           │                    │
                    ┌──────┴───────┐            │
                    │  Converge    │────────────┘
                    │  Worker      │
                    └──────────────┘
                           │
                    ┌──────┴───────┐
                    │  OTEL        │
                    │  Collector   │
                    └──────────────┘
```

- **API Server**: FastAPI/uvicorn on port 9876. Handles HTTP API, GitHub webhooks, dashboard.
- **Worker**: Separate process. Polls queue, processes intents, publishes results to GitHub.
- **PostgreSQL**: Primary data store. SQLite available as fallback.
- **OTEL Collector**: Optional. Receives traces and metrics via OTLP.

## Startup

### Docker Compose (development/staging)

```bash
# Full stack
docker compose up -d

# Check health
curl http://localhost:9876/health/ready

# With observability
docker compose --profile observability up -d

# View logs
docker compose logs -f converge
docker compose logs -f converge-worker
```

### Kubernetes

```bash
# Apply manifests
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment-api.yaml
kubectl apply -f k8s/deployment-worker.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml

# Verify
kubectl get pods -l app=converge
kubectl logs -l component=api --tail=50
kubectl logs -l component=worker --tail=50
```

### CLI (local development)

```bash
# API server
converge serve --host 127.0.0.1 --port 9876

# Worker (separate terminal)
converge worker
```

### API prefixes

- Canonical prefix: `/v1`
- Compatibility mirror: `/api`
- Health/metrics remain outside version prefixes (`/health*`, `/metrics`)

### Command discovery and diagnostics

```bash
# Essential commands
converge --help

# Full command surface (advanced operations)
converge --help-all

# Environment and dependency checks
converge doctor
```

## Shutdown

### Graceful shutdown (recommended)

```bash
# Docker
docker compose stop

# Kubernetes
kubectl scale deployment converge-worker --replicas=0  # drain worker first
kubectl scale deployment converge-api --replicas=0
```

The worker captures SIGTERM and drains the current batch before exiting. The `terminationGracePeriodSeconds` in K8s is set to 60s to allow this.

### Emergency stop

```bash
docker compose kill
# or
kubectl delete pod -l app=converge --force --grace-period=0
```

After an emergency stop, the queue lock may be stale. Clear it:

```bash
converge queue reset --intent-id <existing-intent-id> --clear-lock
```

## Rollback: Postgres → SQLite

If Postgres becomes unavailable and you need to fall back to SQLite:

```bash
# 1. Stop worker and API
docker compose stop converge converge-worker

# 2. Set backend to SQLite
export CONVERGE_DB_BACKEND=sqlite
export CONVERGE_DB_PATH=/data/converge/state.db

# 3. Restart
docker compose up -d converge
```

Note: data in Postgres is NOT automatically copied to SQLite. Use the backfill script for migration:

```bash
# Postgres → SQLite backfill
python scripts/backfill_sqlite_to_pg.py --reverse --source "$CONVERGE_PG_DSN" --target /data/state.db
```

## Key Rotation

API keys should be rotated periodically:

```bash
# Rotate via API (admin key required)
curl -X POST http://localhost:9876/api/auth/keys/rotate \
  -H "x-api-key: CURRENT_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"grace_period_seconds": 3600}'
```

The response contains the new key. Update `CONVERGE_API_KEYS` env var with the new key. The old key remains valid for the grace period.

### GitHub App private key rotation

1. Generate new key in GitHub App settings
2. Save as PEM file
3. Update K8s secret:
   ```bash
   kubectl create secret generic converge-github-app-key \
     --from-file=private-key.pem=/path/to/new-key.pem \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
4. Restart API and worker to pick up new key

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `CONVERGE_DB_BACKEND` | `sqlite` | Storage backend: `sqlite` or `postgres` |
| `CONVERGE_DB_PATH` | `.converge/state.db` | SQLite database path |
| `CONVERGE_PG_DSN` | — | PostgreSQL connection string |
| `CONVERGE_AUTH_REQUIRED` | `1` | Enable API authentication |
| `CONVERGE_API_KEYS` | — | Comma-separated `key:role:actor:tenant:scopes` |
| `CONVERGE_RATE_LIMIT_ENABLED` | `1` | Enable per-tenant rate limiting |
| `CONVERGE_RATE_LIMIT_RPM` | `120` | Requests per minute per tenant |
| `CONVERGE_GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `CONVERGE_GITHUB_APP_ID` | — | GitHub App numeric ID |
| `CONVERGE_GITHUB_APP_PRIVATE_KEY_PATH` | — | Path to PEM private key file |
| `CONVERGE_GITHUB_APP_PRIVATE_KEY` | — | PEM contents (fallback, not recommended for prod) |
| `CONVERGE_GITHUB_INSTALLATION_ID` | — | GitHub App installation ID |
| `CONVERGE_GITHUB_DEFAULT_TENANT` | — | Default tenant for PR-created intents |
| `CONVERGE_WORKER_POLL_INTERVAL` | `5` | Worker poll interval (seconds) |
| `CONVERGE_WORKER_BATCH_SIZE` | `20` | Max intents per worker cycle |
| `CONVERGE_WORKER_MAX_RETRIES` | `3` | Max retries before intent rejected |
| `CONVERGE_WORKER_TARGET` | `main` | Target branch for queue processing |
| `CONVERGE_WORKER_AUTO_CONFIRM` | `0` | Auto-confirm merges (`1` = yes) |
| `CONVERGE_FF_<FLAG_NAME>` | — | Override feature flag (`1`/`true` = enable) |
| `CONVERGE_FF_<FLAG_NAME>_MODE` | — | Override flag mode (`shadow`/`enforce`) |

## Verification Debt Management (Phase 5)

### Check debt score

```bash
converge --db $CONVERGE_DB_PATH verification debt
```

Output includes:
- **debt_score** (0-100): composite score
- **staleness_score**: fraction of stale intents (> 24h)
- **queue_pressure_score**: active intents vs capacity
- **review_backlog_score**: pending reviews vs threshold
- **conflict_pressure_score**: merge (70%) + semantic (30%) conflict rate
- **retry_pressure_score**: retried intents ratio
- **status**: green (0-30), yellow (30-70), red (70-100)

### Reducing debt

| Factor | Action |
|---|---|
| High staleness | Process or reject old intents |
| High queue pressure | Increase capacity or pause intake |
| High review backlog | Assign reviewers, escalate overdue |
| High conflict rate | Resolve semantic conflicts, investigate merge failures |
| High retry pressure | Investigate failing intents, check CI status |

### API endpoint

```
GET /api/verification/debt           — current debt snapshot
GET /api/verification/debt?tenant_id=X  — tenant-scoped
```

---

## Review Task Escalation (Phase 6)

### Check review backlog

```bash
converge --db $CONVERGE_DB_PATH review list --status pending
converge --db $CONVERGE_DB_PATH review summary
```

### Assign, complete, cancel, escalate

```bash
converge --db $CONVERGE_DB_PATH review assign --task-id <id> --reviewer <agent>
converge --db $CONVERGE_DB_PATH review complete --task-id <id> --resolution approved
converge --db $CONVERGE_DB_PATH review cancel --task-id <id>
converge --db $CONVERGE_DB_PATH review escalate --task-id <id>
```

### Check SLA compliance

```bash
converge --db $CONVERGE_DB_PATH review sla-check
```

### Escalation criteria

| Condition | Action |
|---|---|
| Pending > 4 hours | Auto-assign to available reviewer |
| Assigned > 8 hours | Escalate to team lead |
| SLA breach detected | Page on-call + escalate |
| Backlog > 10 tasks | Switch intake to throttle mode |

### API endpoints

```
GET  /api/reviews             — list review tasks (filterable by status)
GET  /api/reviews/summary     — counts by status
```

---

## Security Finding Triage (Phase 7)

### Check security status

```bash
converge --db $CONVERGE_DB_PATH security summary
converge --db $CONVERGE_DB_PATH security findings --severity critical
converge --db $CONVERGE_DB_PATH security findings --severity high
```

### Trigger a security scan

```bash
converge --db $CONVERGE_DB_PATH security scan --intent-id <id>
```

### Triage workflow

1. Run `security summary` daily
2. **Critical findings**: Immediate action — block the intent, notify owner
3. **High findings**: Triage within 4 hours — assess if false positive
4. **Medium/Low**: Batch review weekly

### Security gate thresholds

The policy engine evaluates security findings as the 4th gate:

| Risk level | Max critical | Max high |
|---|---|---|
| low | 0 | 5 |
| medium | 0 | 2 |
| high | 0 | 0 |
| critical | 0 | 0 |

### API endpoints

```
GET  /api/security/findings        — list findings (filterable)
GET  /api/security/findings/counts — severity breakdown
GET  /api/security/scans           — scan history
GET  /api/security/summary         — dashboard summary
POST /api/security/scan            — trigger scan (requires operator)
```

---

## Intake Mode Management (Phase 8)

### Check current mode

```bash
converge --db $CONVERGE_DB_PATH intake status
```

### Change intake mode

```bash
converge --db $CONVERGE_DB_PATH intake set-mode open
converge --db $CONVERGE_DB_PATH intake set-mode throttle
converge --db $CONVERGE_DB_PATH intake set-mode pause
converge --db $CONVERGE_DB_PATH intake set-mode auto
```

### When to change mode

| Condition | Recommended mode |
|---|---|
| Queue healthy, debt < 30 | `open` |
| Queue backlog growing, debt 30-70 | `throttle` |
| Queue overloaded, debt > 70 | `pause` |
| Incident in progress | `pause` |
| After incident resolved | `throttle` then `open` |

### API endpoints

```
GET  /api/intake/status   — current mode and stats
POST /api/intake/mode     — set mode (requires operator role)
```

---

## Semantic Conflict Triage

### View active conflicts

```bash
converge --db $CONVERGE_DB_PATH semantic conflict-list
converge --db $CONVERGE_DB_PATH semantic conflicts
```

### Resolve a conflict

```bash
converge --db $CONVERGE_DB_PATH semantic conflict-resolve \
  --intent-a <intent-id-1> --intent-b <intent-id-2> --resolution "overlapping scope accepted"
```

### Triage workflow

1. Check `semantic conflict-list` for new conflicts
2. Inspect the two intents involved
3. If they can coexist: resolve with reason
4. If truly conflicting: reject one intent via policy
5. Check verification debt to see impact: `converge verification debt`

**Escalate if**: > 5 unresolved conflicts for > 24 hours.

---

## Semantic Index Management

### Reindex embeddings

Use when embeddings are stale or similarity model has changed.

```bash
converge --db $CONVERGE_DB_PATH semantic status
converge --db $CONVERGE_DB_PATH semantic reindex
converge --db $CONVERGE_DB_PATH semantic status   # verify
```

### Index a specific intent

```bash
converge --db $CONVERGE_DB_PATH semantic index --intent-id <id>
```

---

## Audit Chain Verification

### Initialize chain

Run once after deployment or database migration.

```bash
converge --db $CONVERGE_DB_PATH audit init-chain
```

### Verify chain integrity

```bash
converge --db $CONVERGE_DB_PATH audit verify-chain
```

**If invalid**: Chain hash mismatch indicates potential tampering or data corruption.
1. Check for database restores or manual edits
2. Re-initialize: `converge audit init-chain`

---

## Feature Flag Operations (Phase 9)

### List all flags

```bash
curl -s http://localhost:9876/api/flags | jq .
```

### Toggle a flag at runtime

```bash
curl -X POST http://localhost:9876/api/flags/security_adapters \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

### Override via environment

```bash
export CONVERGE_FF_SECURITY_ADAPTERS=false
export CONVERGE_FF_SEMANTIC_CONFLICTS_MODE=enforce
```

### Override via config file

Create `.converge/flags.json`:

```json
{
  "security_adapters": false,
  "semantic_conflicts": {"enabled": true, "mode": "enforce"}
}
```

### Flag defaults

| Flag | Default | Mode | Purpose |
|---|---|---|---|
| intent_links | enabled | — | Commit-intent link tracking |
| archaeology_enhanced | enabled | — | Enhanced git history analysis |
| intent_semantics | enabled | — | Semantic embeddings |
| origin_policy | enabled | — | Origin-type policy overrides |
| verification_debt | enabled | — | Debt tracking |
| review_tasks | enabled | — | Human review workflow |
| security_adapters | enabled | — | Security scanner integration |
| intake_control | enabled | — | Adaptive intake throttling |
| semantic_conflicts | enabled | shadow | Semantic conflict detection |
| plan_coordination | enabled | — | Plan dependency enforcement |
| audit_chain | enabled | — | Event tamper-evidence chain |
| code_ownership | **disabled** | — | Code-area ownership SoD |
| pre_eval_harness | enabled | shadow | Pre-PR evaluation harness |
| semantic_embeddings_model | enabled | deterministic | Embedding provider mode |
| risk_auto_classify | enabled | enforce | Auto-reclassify risk level from scores |
| advisory_locks | **disabled** | shadow | PostgreSQL advisory queue locks |
| llm_review_advisor | **disabled** | shadow | LLM-powered review summaries |
| coherence_feedback | enabled | — | Suggestion loop for coherence harness |
| notifications | **disabled** | shadow | Outbound webhook notifications |

**Priority**: env vars > config file > defaults.

---

## Pre-PR Evaluation Harness

### Evaluate an intent before creation

```bash
cat > /tmp/draft-intent.json <<'EOF'
{
  "source": "feature/x",
  "target": "main",
  "risk_level": "medium",
  "semantic": {
    "problem_statement": "Authentication flow lacks refresh token support",
    "objective": "Add refresh token lifecycle handling"
  }
}
EOF

converge --db $CONVERGE_DB_PATH harness evaluate \
  --file /tmp/draft-intent.json --mode shadow
```

### Modes

- **shadow** (default): Evaluates but always passes. Score and signals logged.
- **enforce**: Blocks intents with score < 0.5.

---

## Plan Coordination Troubleshooting

A **plan** groups N intents via `plan_id`. Each intent can declare `dependencies`
(list of intent IDs that must be MERGED before processing).

### Common issues

**Intent stuck waiting for dependency**: Check blocking intent status.

```bash
converge --db $CONVERGE_DB_PATH intent status --intent-id <blocked-id>
converge --db $CONVERGE_DB_PATH intent status --intent-id <dependency-id>
```

**Circular dependency**: Both intents skip indefinitely. Remove one dependency.

**Dependency REJECTED**: Dependent intent will never process. Re-create the
dependency or remove it from the dependent intent.

---

## Emergency Procedures

### Queue overload

```bash
# 1. Pause intake
converge --db $CONVERGE_DB_PATH intake set-mode pause

# 2. Check debt
converge --db $CONVERGE_DB_PATH verification debt

# 3. Process critical intents only
converge --db $CONVERGE_DB_PATH queue run --limit 5 --target main

# 4. Resume when stable
converge --db $CONVERGE_DB_PATH intake set-mode throttle
# ... wait for debt to decrease ...
converge --db $CONVERGE_DB_PATH intake set-mode open
```

### Disable a misbehaving feature

```bash
curl -X POST http://localhost:9876/api/flags/<flag_name> \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

### Security incident

```bash
# 1. Pause intake
converge --db $CONVERGE_DB_PATH intake set-mode pause

# 2. Run security scan
converge --db $CONVERGE_DB_PATH security scan

# 3. Review critical findings
converge --db $CONVERGE_DB_PATH security findings --severity critical

# 4. Block affected intents via policy review
```

---

## Troubleshooting

### Queue stuck / lock not released

```bash
# Check lock status
converge queue inspect --only-actionable

# Force-release lock
converge queue reset --intent-id <existing-intent-id> --clear-lock
```

### Worker not processing

1. Check worker logs: `kubectl logs -l component=worker`
2. Verify DB connectivity: `kubectl exec -it deploy/converge-worker -- python -c "from converge import event_log; event_log.init(); print('OK')"`
3. Check for stale lock (see above)
4. Verify intents exist in VALIDATED status: `curl http://localhost:9876/api/queue/state`

### GitHub check-runs not appearing

1. Verify GitHub App is installed on the repository
2. Check env vars: `CONVERGE_GITHUB_APP_ID`, `CONVERGE_GITHUB_INSTALLATION_ID`
3. Check private key is mounted: `ls /secrets/github-app/`
4. Check worker logs for GitHub API errors

### High latency / 429 errors

1. Check rate limit: `curl http://localhost:9876/metrics | grep rate_limit`
2. Increase RPM: set `CONVERGE_RATE_LIMIT_RPM=300`
3. Scale API pods: `kubectl scale deployment converge-api --replicas=4`

### Database connection errors (Postgres)

1. Verify Postgres is running: `pg_isready -h localhost -p 5432`
2. Check DSN: `echo $CONVERGE_PG_DSN`
3. Test connection: `psql "$CONVERGE_PG_DSN" -c "SELECT 1"`
4. Check connection pool: review worker/API logs for pool exhaustion

## Doctor (Environment Validation)

### Run diagnostic check

```bash
converge --db $CONVERGE_DB_PATH doctor
```

Validates environment setup, database connectivity, feature flag state, and reports overall health. Use as a first step when something isn't working.

---

## Coherence Harness Operations

### Initialize harness config

```bash
converge --db $CONVERGE_DB_PATH coherence init
```

Creates `.converge/coherence_harness.json` with the default question template. Edit this file to add project-specific coherence questions.

### List configured questions

```bash
converge --db $CONVERGE_DB_PATH coherence list
```

### Run harness against current state

```bash
converge --db $CONVERGE_DB_PATH coherence run
```

Executes all enabled questions, computes score (0-100), and returns verdict (PASS/WARN/FAIL).

### Update baselines

```bash
converge --db $CONVERGE_DB_PATH coherence baseline
```

Captures current values as baselines for future comparisons. Run after known-good state changes (e.g., after a major release).

### Suggest new questions from failure patterns

```bash
converge --db $CONVERGE_DB_PATH coherence suggest --lookback-days 90
```

Analyzes recent failure history and suggests new harness questions based on recurring patterns.

### Accept a suggestion

```bash
converge --db $CONVERGE_DB_PATH coherence accept --suggestion-id <id>
```

Adds the suggested question to the harness configuration.

---

## Export Operations

### Export decision dataset

```bash
converge --db $CONVERGE_DB_PATH export decisions --format jsonl --output decisions.jsonl
converge --db $CONVERGE_DB_PATH export decisions --format csv --output decisions.csv
```

Exports historical decisions for external analysis or calibration.

---

## Secrets Management

For production, replace K8s Secret manifests with:

- **HashiCorp Vault**: Mount secrets via CSI driver or Vault Agent injector
- **External Secrets Operator**: Sync from AWS Secrets Manager, GCP Secret Manager, or Azure Key Vault
- **Sealed Secrets**: Encrypt secrets in git using Bitnami sealed-secrets

Path to Vault integration (not implemented):
1. Install Vault Agent Injector in K8s
2. Annotate deployments with `vault.hashicorp.com/agent-inject-secret-*`
3. Reference paths like `secret/data/converge/api-keys`
