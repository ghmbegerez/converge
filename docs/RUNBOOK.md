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
converge queue reset --intent-id any --clear-lock
# or via API
curl -X POST http://localhost:9876/api/queue/reset -d '{"intent_id": "any", "clear_lock": true}'
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

## Troubleshooting

### Queue stuck / lock not released

```bash
# Check lock status
converge queue inspect --only-actionable

# Force-release lock
converge queue reset --intent-id any --clear-lock
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

## Secrets Management

For production, replace K8s Secret manifests with:

- **HashiCorp Vault**: Mount secrets via CSI driver or Vault Agent injector
- **External Secrets Operator**: Sync from AWS Secrets Manager, GCP Secret Manager, or Azure Key Vault
- **Sealed Secrets**: Encrypt secrets in git using Bitnami sealed-secrets

Path to Vault integration (not implemented):
1. Install Vault Agent Injector in K8s
2. Annotate deployments with `vault.hashicorp.com/agent-inject-secret-*`
3. Reference paths like `secret/data/converge/api-keys`
