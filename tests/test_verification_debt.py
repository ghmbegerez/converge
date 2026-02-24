"""Tests for verification debt projection (AR-28..AR-31)."""

from datetime import datetime, timedelta, timezone

from conftest import make_intent

from converge import event_log
from converge.event_types import EventType
from converge.models import Event, Intent, ReviewStatus, ReviewTask, RiskLevel, Status, now_iso
from converge.projections.verification import (
    _DEBT_GREEN,
    _DEBT_YELLOW,
    _W_CONFLICT,
    _W_QUEUE_PRESSURE,
    _W_RETRY,
    _W_REVIEW_BACKLOG,
    _W_STALENESS,
    verification_debt,
)
from converge.reviews import request_review


def _old_timestamp(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# ---------------------------------------------------------------------------
# TestEmptyState
# ---------------------------------------------------------------------------

class TestEmptyState:
    """Debt on an empty database."""

    def test_zero_debt_empty_db(self, db_path):
        """Empty database → zero debt."""
        snap = verification_debt()
        assert snap.debt_score == 0.0
        assert snap.status == "green"

    def test_breakdown_empty(self, db_path):
        """All factors zero on empty DB."""
        snap = verification_debt()
        assert snap.staleness_score == 0.0
        assert snap.queue_pressure_score == 0.0
        assert snap.review_backlog_score == 0.0
        assert snap.conflict_pressure_score == 0.0
        assert snap.retry_pressure_score == 0.0

    def test_emits_event(self, db_path):
        """Debt computation emits a snapshot event."""
        verification_debt()
        events = event_log.query(event_type=EventType.VERIFICATION_DEBT_SNAPSHOT)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# TestStaleness
# ---------------------------------------------------------------------------

class TestStaleness:
    """Staleness factor: old intents increase debt."""

    def test_fresh_intents_no_staleness(self, db_path):
        """Recently created intents contribute zero staleness."""
        for i in range(5):
            make_intent(f"fresh-{i}")
        snap = verification_debt()
        assert snap.staleness_score == 0.0

    def test_stale_intents_increase_debt(self, db_path):
        """Intents older than threshold contribute staleness."""
        for i in range(4):
            make_intent(f"stale-{i}", created_at=_old_timestamp(48))
        snap = verification_debt(stale_hours=24)
        # All 4 intents are stale (older than 24h), ratio=1.0 → full weight
        assert snap.staleness_score == _W_STALENESS

    def test_mixed_staleness(self, db_path):
        """Mix of fresh and stale intents."""
        # 2 stale + 2 fresh = 50% staleness
        for i in range(2):
            make_intent(f"stale-{i}", created_at=_old_timestamp(48))
        for i in range(2):
            make_intent(f"fresh-{i}")
        snap = verification_debt(stale_hours=24)
        assert snap.staleness_score == _W_STALENESS * 0.5


# ---------------------------------------------------------------------------
# TestQueuePressure
# ---------------------------------------------------------------------------

class TestQueuePressure:
    """Queue depth pressure factor."""

    def test_under_capacity(self, db_path):
        """Active intents below capacity → partial pressure."""
        for i in range(10):
            make_intent(f"q-{i}")
        snap = verification_debt(queue_capacity=50)
        # 10/50 = 0.2 → 0.2 * 20 = 4.0
        assert snap.queue_pressure_score == round(10 / 50 * _W_QUEUE_PRESSURE, 1)

    def test_at_capacity(self, db_path):
        """Active intents at capacity → full pressure."""
        for i in range(50):
            make_intent(f"q-{i}")
        snap = verification_debt(queue_capacity=50)
        assert snap.queue_pressure_score == _W_QUEUE_PRESSURE

    def test_over_capacity_capped(self, db_path):
        """Active intents above capacity → capped at 1.0."""
        for i in range(100):
            make_intent(f"q-{i}")
        snap = verification_debt(queue_capacity=50)
        assert snap.queue_pressure_score == _W_QUEUE_PRESSURE

    def test_merged_intents_excluded(self, db_path):
        """Merged/rejected intents don't count as active."""
        for i in range(5):
            make_intent(f"merged-{i}", status=Status.MERGED)
        for i in range(3):
            make_intent(f"active-{i}")
        snap = verification_debt(queue_capacity=50)
        # Only 3 active, not 8
        assert snap.queue_pressure_score == round(3 / 50 * _W_QUEUE_PRESSURE, 1)


# ---------------------------------------------------------------------------
# TestReviewBacklog
# ---------------------------------------------------------------------------

class TestReviewBacklog:
    """Review backlog factor."""

    def test_no_reviews(self, db_path):
        """No pending reviews → zero review debt."""
        make_intent("r-1")
        snap = verification_debt()
        assert snap.review_backlog_score == 0.0

    def test_pending_reviews_increase_debt(self, db_path):
        """Pending review tasks increase debt."""
        make_intent("r-1")
        for i in range(5):
            request_review("r-1")
        snap = verification_debt(review_capacity=10)
        # 5/10 = 0.5 → 0.5 * 25 = 12.5
        assert snap.review_backlog_score == round(5 / 10 * _W_REVIEW_BACKLOG, 1)

    def test_completed_reviews_excluded(self, db_path):
        """Completed reviews don't count."""
        make_intent("r-1")
        task = request_review("r-1")
        # Complete it
        from converge.reviews import assign_review, complete_review
        assign_review(task.id, "reviewer-1")
        complete_review(task.id, resolution="approved")
        snap = verification_debt(review_capacity=10)
        assert snap.review_backlog_score == 0.0


# ---------------------------------------------------------------------------
# TestConflictPressure
# ---------------------------------------------------------------------------

class TestConflictPressure:
    """Conflict pressure from simulations."""

    def test_no_simulations(self, db_path):
        """No simulations → zero conflict pressure."""
        snap = verification_debt()
        assert snap.conflict_pressure_score == 0.0

    def test_all_mergeable(self, db_path):
        """All simulations mergeable → zero conflict."""
        for i in range(5):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": True},
            ))
        snap = verification_debt()
        assert snap.conflict_pressure_score == 0.0

    def test_all_conflicting_merge_only(self, db_path):
        """All sims conflicting, no semantic → 70% of conflict weight."""
        for i in range(5):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        snap = verification_debt()
        # merge_rate=1.0 * 0.7 + semantic_rate=0.0 * 0.3 = 0.7
        assert snap.conflict_pressure_score == round(0.7 * _W_CONFLICT, 1)

    def test_all_conflicting_with_semantic(self, db_path):
        """All merge + full semantic conflicts → full conflict weight."""
        for i in range(5):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        # 10+ semantic conflicts → full semantic pressure
        for i in range(10):
            event_log.append(Event(
                event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
                payload={"conflict_id": f"sc-{i}"},
            ))
        snap = verification_debt()
        # merge_rate=1.0 * 0.7 + semantic_rate=1.0 * 0.3 = 1.0
        assert snap.conflict_pressure_score == _W_CONFLICT

    def test_semantic_only_conflict(self, db_path):
        """No merge conflicts, only semantic → 30% of conflict weight."""
        # All mergeable sims
        for i in range(3):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": True},
            ))
        # 10 semantic conflicts → full semantic pressure
        for i in range(10):
            event_log.append(Event(
                event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
                payload={"conflict_id": f"sc-{i}"},
            ))
        snap = verification_debt()
        # merge_rate=0.0 * 0.7 + semantic_rate=1.0 * 0.3 = 0.3
        assert snap.conflict_pressure_score == round(0.3 * _W_CONFLICT, 1)


# ---------------------------------------------------------------------------
# TestRetryPressure
# ---------------------------------------------------------------------------

class TestRetryPressure:
    """Retry pressure from retried intents."""

    def test_no_retries(self, db_path):
        """No retries → zero retry pressure."""
        for i in range(5):
            make_intent(f"nr-{i}")
        snap = verification_debt()
        assert snap.retry_pressure_score == 0.0

    def test_all_retrying(self, db_path):
        """All active intents retrying → full retry weight."""
        for i in range(5):
            make_intent(f"rt-{i}", retries=2)
        snap = verification_debt()
        assert snap.retry_pressure_score == _W_RETRY

    def test_partial_retries(self, db_path):
        """Half retrying → half weight."""
        for i in range(4):
            make_intent(f"nr-{i}")
        for i in range(4):
            make_intent(f"rt-{i}", retries=1)
        snap = verification_debt()
        # 4/8 = 0.5 → 0.5 * 15 = 7.5
        assert snap.retry_pressure_score == round(0.5 * _W_RETRY, 1)


# ---------------------------------------------------------------------------
# TestStatusThresholds
# ---------------------------------------------------------------------------

class TestStatusThresholds:
    """Debt status color coding."""

    def test_green_low_debt(self, db_path):
        """Score <= 30 → green."""
        snap = verification_debt()
        assert snap.debt_score <= _DEBT_GREEN
        assert snap.status == "green"

    def test_yellow_medium_debt(self, db_path):
        """Score between 30 and 70 → yellow."""
        # Create enough pressure to push into yellow
        # 50 active intents at capacity + all stale → 25 + 20 = 45
        for i in range(50):
            make_intent(f"d-{i}", created_at=_old_timestamp(48))
        snap = verification_debt(queue_capacity=50, stale_hours=24)
        assert snap.debt_score > _DEBT_GREEN
        assert snap.status in ("yellow", "red")

    def test_to_dict(self, db_path):
        """Snapshot serializes correctly."""
        snap = verification_debt()
        d = snap.to_dict()
        assert "debt_score" in d
        assert "breakdown" in d
        assert "status" in d
        assert d["status"] == "green"


# ---------------------------------------------------------------------------
# TestCompositeScore
# ---------------------------------------------------------------------------

class TestCompositeScore:
    """Composite debt score from all factors."""

    def test_weights_sum_to_100(self, db_path):
        """All weights sum to 100."""
        total = _W_STALENESS + _W_QUEUE_PRESSURE + _W_REVIEW_BACKLOG + _W_CONFLICT + _W_RETRY
        assert total == 100.0

    def test_max_debt_is_100(self, db_path):
        """Maximum possible debt is 100."""
        # All factors at max: 50 stale intents at capacity, all retrying,
        # all conflicting (merge + semantic), 10+ pending reviews
        for i in range(50):
            make_intent(f"max-{i}", created_at=_old_timestamp(48), retries=3)
        for i in range(50):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        # AR-22: semantic conflicts needed for full conflict pressure
        for i in range(10):
            event_log.append(Event(
                event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
                payload={"conflict_id": f"sc-{i}"},
            ))
        for i in range(10):
            request_review(f"max-{i}")
        snap = verification_debt(queue_capacity=50, stale_hours=24, review_capacity=10)
        assert snap.debt_score == 100.0
        assert snap.status == "red"


# ---------------------------------------------------------------------------
# TestComplianceIntegration (AR-30)
# ---------------------------------------------------------------------------

class TestComplianceIntegration:
    """Debt check integrated into compliance report."""

    def test_low_debt_passes_compliance(self, db_path):
        """Low debt passes the debt_score compliance check."""
        from converge.projections.compliance import compliance_report
        report = compliance_report()
        debt_check = [c for c in report.checks if c["name"] == "debt_score"]
        assert len(debt_check) == 1
        assert debt_check[0]["passed"] is True

    def test_high_debt_fails_compliance(self, db_path):
        """High debt triggers compliance alert."""
        # Push debt high: max everything
        for i in range(50):
            make_intent(f"c-{i}", created_at=_old_timestamp(48), retries=3)
        for i in range(50):
            event_log.append(Event(
                event_type=EventType.SIMULATION_COMPLETED,
                payload={"mergeable": False},
            ))
        from converge.projections.compliance import compliance_report
        report = compliance_report(thresholds={"max_debt_score": 40.0})
        debt_check = [c for c in report.checks if c["name"] == "debt_score"]
        assert len(debt_check) == 1
        assert debt_check[0]["passed"] is False
        debt_alerts = [a for a in report.alerts if "debt_score" in a.get("alert", "")]
        assert len(debt_alerts) == 1

    def test_custom_debt_threshold(self, db_path):
        """Custom max_debt_score threshold is respected."""
        # Small queue pressure only
        for i in range(10):
            make_intent(f"ct-{i}")
        from converge.projections.compliance import compliance_report
        # Very strict threshold
        report = compliance_report(thresholds={"max_debt_score": 1.0})
        debt_check = [c for c in report.checks if c["name"] == "debt_score"]
        assert len(debt_check) == 1
        # 10/50 * 20 = 4.0 debt > 1.0 threshold → fail
        assert debt_check[0]["passed"] is False


# ---------------------------------------------------------------------------
# TestTenantIsolation
# ---------------------------------------------------------------------------

class TestTenantIsolation:
    """Per-tenant debt computation."""

    def test_tenant_scoped(self, db_path):
        """Debt computed per-tenant only includes that tenant's data."""
        for i in range(20):
            make_intent(f"a-{i}", tenant_id="tenant-A")
        for i in range(5):
            make_intent(f"b-{i}", tenant_id="tenant-B")
        snap_a = verification_debt(tenant_id="tenant-A")
        snap_b = verification_debt(tenant_id="tenant-B")
        assert snap_a.queue_pressure_score > snap_b.queue_pressure_score

    def test_global_includes_all(self, db_path):
        """Global debt includes all tenants."""
        for i in range(10):
            make_intent(f"a-{i}", tenant_id="tenant-A")
        for i in range(10):
            make_intent(f"b-{i}", tenant_id="tenant-B")
        snap_global = verification_debt()
        # Global should see 20 intents
        assert snap_global.breakdown["active_intents"] == 20


# ---------------------------------------------------------------------------
# TestIntakeIntegration
# ---------------------------------------------------------------------------

class TestIntakeIntegration:
    """Debt feeds into intake mode computation."""

    def test_high_debt_affects_intake(self, db_path):
        """High verification debt can trigger intake throttle via effective_score."""
        # Create high debt: 50+ stale intents at capacity with retries
        for i in range(60):
            make_intent(f"hd-{i}", created_at=_old_timestamp(48), retries=2)
        from converge.intake import _compute_auto_mode, DEFAULT_INTAKE_CONFIG
        cfg = dict(DEFAULT_INTAKE_CONFIG)
        mode, signals = _compute_auto_mode(config=cfg)
        # With high debt, effective_score should be low
        assert signals["debt_score"] > 30
        assert signals["effective_score"] < signals["health_score"]
