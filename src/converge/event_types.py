"""Event type registry: single source of truth for all event type strings.

Extracted from models.py to keep domain model module under LOC limit.
"""


class EventType:
    # Simulation
    SIMULATION_COMPLETED = "simulation.completed"
    # Checks
    CHECK_COMPLETED = "check.completed"
    # Risk
    RISK_EVALUATED = "risk.evaluated"
    RISK_SHADOW_EVALUATED = "risk.shadow_evaluated"
    RISK_POLICY_UPDATED = "risk.policy_updated"
    # Policy
    POLICY_EVALUATED = "policy.evaluated"
    # Intent lifecycle
    INTENT_CREATED = "intent.created"
    INTENT_STATUS_CHANGED = "intent.status_changed"
    INTENT_VALIDATED = "intent.validated"
    INTENT_BLOCKED = "intent.blocked"
    INTENT_REJECTED = "intent.rejected"
    INTENT_REQUEUED = "intent.requeued"
    INTENT_MERGED = "intent.merged"
    INTENT_MERGE_FAILED = "intent.merge_failed"
    INTENT_DEPENDENCY_BLOCKED = "intent.dependency_blocked"
    # Linkage
    INTENT_LINKED_COMMIT = "intent.linked.commit"
    INTENT_LINK_REMOVED = "intent.link.removed"
    # Queue
    QUEUE_PROCESSED = "queue.processed"
    QUEUE_RESET = "queue.reset"
    # Health
    HEALTH_SNAPSHOT = "health.snapshot"
    HEALTH_CHANGE_SNAPSHOT = "health.change_snapshot"
    HEALTH_PREDICTION = "health.prediction"
    # Compliance
    COMPLIANCE_THRESHOLDS_UPDATED = "compliance.thresholds_updated"
    # Agent
    AGENT_POLICY_UPDATED = "agent.policy_updated"
    AGENT_AUTHORIZED = "agent.authorized"
    # Semantic
    EMBEDDING_GENERATED = "embedding.generated"
    EMBEDDING_REINDEXED = "embedding.reindexed"
    SEMANTIC_CONFLICT_DETECTED = "semantic_conflict.detected"
    SEMANTIC_CONFLICT_RESOLVED = "semantic_conflict.resolved"
    # Review
    REVIEW_REQUESTED = "review.requested"
    REVIEW_ASSIGNED = "review.assigned"
    REVIEW_REASSIGNED = "review.reassigned"
    REVIEW_ESCALATED = "review.escalated"
    REVIEW_COMPLETED = "review.completed"
    REVIEW_CANCELLED = "review.cancelled"
    REVIEW_SLA_BREACHED = "review.sla.breached"
    # Analytics
    CALIBRATION_COMPLETED = "calibration.completed"
    DATASET_EXPORTED = "dataset.exported"
    # Integrations
    WEBHOOK_RECEIVED = "webhook.received"
    # GitHub
    GITHUB_DECISION_PUBLISHED = "github.decision_published"
    GITHUB_DECISION_PUBLISH_FAILED = "github.decision_publish_failed"
    # Merge Queue
    MERGE_GROUP_CHECKS_REQUESTED = "merge_group.checks_requested"
    MERGE_GROUP_DESTROYED = "merge_group.destroyed"
    # Verification debt
    VERIFICATION_DEBT_SNAPSHOT = "verification.debt.snapshot"
    # Intake
    INTAKE_ACCEPTED = "intake.accepted"
    INTAKE_THROTTLED = "intake.throttled"
    INTAKE_REJECTED = "intake.rejected"
    INTAKE_MODE_CHANGED = "intake.mode_changed"
    # Security scanning
    SECURITY_SCAN_STARTED = "security.scan.started"
    SECURITY_SCAN_COMPLETED = "security.scan.completed"
    SECURITY_FINDING_DETECTED = "security.finding.detected"
    # Audit chain
    CHAIN_INITIALIZED = "audit.chain.initialized"
    CHAIN_VERIFIED = "audit.chain.verified"
    CHAIN_TAMPER_DETECTED = "audit.chain.tamper_detected"
    # Separation of duties
    SOD_VIOLATION = "policy.sod.violation"
    # Pre-evaluation harness
    INTENT_PRE_EVALUATED = "intent.pre_evaluated"
    # Feature flags
    FEATURE_FLAG_CHANGED = "feature_flag.changed"
    # Coherence harness
    COHERENCE_EVALUATED = "coherence.evaluated"
    COHERENCE_BASELINE_UPDATED = "coherence.baseline_updated"
    COHERENCE_INCONSISTENCY = "coherence.inconsistency"
    # Risk reclassification (Initiative 2)
    RISK_LEVEL_RECLASSIFIED = "risk.level_reclassified"
    # LLM review advisor (Initiative 4)
    REVIEW_ANALYSIS_GENERATED = "review.analysis_generated"
    REVIEW_ANALYSIS_FAILED = "review.analysis_failed"
    # Coherence feedback (Initiative 5)
    COHERENCE_SUGGESTION = "coherence.suggestion"
    COHERENCE_SUGGESTION_ACCEPTED = "coherence.suggestion_accepted"
    # Notifications (Initiative 6)
    NOTIFICATION_SENT = "notification.sent"
    NOTIFICATION_FAILED = "notification.failed"
    # Worker
    WORKER_STARTED = "worker.started"
    WORKER_STOPPED = "worker.stopped"
    WORKER_HEARTBEAT = "worker.heartbeat"
