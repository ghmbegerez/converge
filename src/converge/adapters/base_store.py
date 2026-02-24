"""Base class for ConvergeStore backends (template method pattern).

All shared SQL logic lives in mixin modules.  Backend-specific concerns
(connection management, SQL dialect placeholders, commit semantics) are
handled by :class:`_StoreDialect`, which subclasses implement.

Application code should depend on the ports, not on this module directly.
"""

from __future__ import annotations

from converge.adapters._core_mixin import (
    CommitLinkStoreMixin,
    EventStoreMixin,
    IntentStoreMixin,
)
from converge.adapters._policy_mixin import (
    DeliveryMixin,
    LockMixin,
    PolicyStoreMixin,
)
from converge.adapters._review_mixin import (
    IntakeStoreMixin,
    ReviewStoreMixin,
    SecurityFindingStoreMixin,
)
from converge.adapters._semantic_mixin import (
    ChainStateMixin,
    EmbeddingStoreMixin,
)
from converge.adapters._store_dialect import _StoreDialect


# ---------------------------------------------------------------------------
# Schema (shared between all backends)
# ---------------------------------------------------------------------------

SCHEMA = """
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
    plan_id        TEXT,
    origin_type    TEXT NOT NULL DEFAULT 'human',
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);
CREATE INDEX IF NOT EXISTS idx_intents_tenant ON intents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_intents_status_source ON intents(status, source);
CREATE INDEX IF NOT EXISTS idx_intents_plan_id ON intents(plan_id);
CREATE INDEX IF NOT EXISTS idx_intents_origin ON intents(origin_type);

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

CREATE TABLE IF NOT EXISTS intent_commit_links (
    intent_id   TEXT NOT NULL,
    repo        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'head',
    observed_at TEXT NOT NULL,
    PRIMARY KEY (intent_id, sha, role)
);
CREATE INDEX IF NOT EXISTS idx_commit_links_intent ON intent_commit_links(intent_id);
CREATE INDEX IF NOT EXISTS idx_commit_links_sha ON intent_commit_links(sha);

CREATE TABLE IF NOT EXISTS review_tasks (
    id              TEXT PRIMARY KEY,
    intent_id       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    reviewer        TEXT,
    priority        INTEGER NOT NULL DEFAULT 3,
    risk_level      TEXT NOT NULL DEFAULT 'medium',
    trigger         TEXT NOT NULL DEFAULT 'policy',
    sla_deadline    TEXT,
    created_at      TEXT NOT NULL,
    assigned_at     TEXT,
    completed_at    TEXT,
    escalated_at    TEXT,
    resolution      TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    tenant_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_tasks_intent ON review_tasks(intent_id);
CREATE INDEX IF NOT EXISTS idx_review_tasks_status ON review_tasks(status);
CREATE INDEX IF NOT EXISTS idx_review_tasks_reviewer ON review_tasks(reviewer);
CREATE INDEX IF NOT EXISTS idx_review_tasks_tenant ON review_tasks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_review_tasks_sla ON review_tasks(sla_deadline);

CREATE TABLE IF NOT EXISTS intent_embeddings (
    intent_id       TEXT NOT NULL,
    model           TEXT NOT NULL,
    dimension       INTEGER NOT NULL,
    checksum        TEXT NOT NULL,
    vector          TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    PRIMARY KEY (intent_id, model)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_intent ON intent_embeddings(intent_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_checksum ON intent_embeddings(checksum);

CREATE TABLE IF NOT EXISTS intake_overrides (
    tenant_id  TEXT PRIMARY KEY,
    mode       TEXT NOT NULL,
    set_by     TEXT NOT NULL DEFAULT 'system',
    set_at     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_findings (
    id          TEXT PRIMARY KEY,
    scanner     TEXT NOT NULL,
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    file        TEXT NOT NULL DEFAULT '',
    line        INTEGER NOT NULL DEFAULT 0,
    rule        TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    intent_id   TEXT,
    tenant_id   TEXT,
    scan_id     TEXT,
    timestamp   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_findings_intent ON security_findings(intent_id);
CREATE INDEX IF NOT EXISTS idx_security_findings_severity ON security_findings(severity);
CREATE INDEX IF NOT EXISTS idx_security_findings_scanner ON security_findings(scanner);
CREATE INDEX IF NOT EXISTS idx_security_findings_tenant ON security_findings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_security_findings_scan_id ON security_findings(scan_id);

CREATE TABLE IF NOT EXISTS event_chain_state (
    chain_id    TEXT PRIMARY KEY DEFAULT 'main',
    last_hash   TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
"""

_MIGRATIONS: list[str] = [
    # AR-47: plan_id field for plan coordination
    "ALTER TABLE intents ADD COLUMN plan_id TEXT",
    # AR-15: origin_type for human/agent/integration distinction
    "ALTER TABLE intents ADD COLUMN origin_type TEXT NOT NULL DEFAULT 'human'",
]


# ---------------------------------------------------------------------------
# BaseConvergeStore — composed from mixins
# ---------------------------------------------------------------------------

class BaseConvergeStore(
    EventStoreMixin,
    IntentStoreMixin,
    CommitLinkStoreMixin,
    EmbeddingStoreMixin,
    ChainStateMixin,
    ReviewStoreMixin,
    IntakeStoreMixin,
    SecurityFindingStoreMixin,
    PolicyStoreMixin,
    LockMixin,
    DeliveryMixin,
    _StoreDialect,
):
    """Abstract base for ConvergeStore backends.

    Subclasses must implement the 6 abstract members defined in
    ``_StoreDialect`` (connection lifecycle, placeholder syntax, upsert
    keyword, constraint-error type, insert-or-ignore syntax, cleanup).

    All public business methods (ports) are provided by the mixin classes.
    """
