-- Migration 001: Initial schema
-- Creates the core tables for Converge.

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    intent_id   TEXT,
    agent_id    TEXT,
    tenant_id   TEXT,
    payload     TEXT NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_intent   ON events(intent_id);
CREATE INDEX IF NOT EXISTS idx_events_tenant   ON events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_events_time     ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_agent    ON events(agent_id);

CREATE TABLE IF NOT EXISTS intents (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    target         TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL DEFAULT 'system',
    risk_level     TEXT NOT NULL DEFAULT 'medium',
    priority       INTEGER NOT NULL DEFAULT 3,
    semantic       TEXT NOT NULL DEFAULT '{}',
    technical      TEXT NOT NULL DEFAULT '{}',
    checks_required TEXT NOT NULL DEFAULT '[]',
    dependencies   TEXT NOT NULL DEFAULT '[]',
    retries        INTEGER NOT NULL DEFAULT 0,
    tenant_id      TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);
CREATE INDEX IF NOT EXISTS idx_intents_tenant ON intents(tenant_id);

CREATE TABLE IF NOT EXISTS agent_policies (
    agent_id   TEXT NOT NULL,
    tenant_id  TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS compliance_thresholds (
    tenant_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_policies (
    tenant_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_locks (
    lock_name   TEXT PRIMARY KEY,
    holder_pid  INTEGER NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

-- Migration tracking table
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

INSERT INTO schema_migrations (version, applied_at)
VALUES (1, NOW()::TEXT)
ON CONFLICT (version) DO NOTHING;
