"""Tests for human review orchestration (AR-32..AR-36)."""

from datetime import datetime, timedelta, timezone

from conftest import make_intent

from converge import event_log
from converge.models import (
    EventType,
    Intent,
    ReviewStatus,
    ReviewTask,
    RiskLevel,
    Status,
    now_iso,
)
from converge.defaults import REVIEW_SLA_HOURS
from converge.reviews import (
    _compute_sla_deadline,
    assign_review,
    cancel_review,
    check_sla_breaches,
    complete_review,
    escalate_review,
    request_review,
    review_summary,
)


# ===================================================================
# AR-32: Review task model and storage
# ===================================================================

class TestReviewTaskModel:
    def test_default_values(self, db_path):
        """ReviewTask has sensible defaults."""
        task = ReviewTask(id="rt-001", intent_id="i-001")
        assert task.status == ReviewStatus.PENDING
        assert task.reviewer is None
        assert task.priority == 3
        assert task.risk_level == RiskLevel.MEDIUM
        assert task.trigger == "policy"
        assert task.resolution is None

    def test_to_dict(self, db_path):
        """to_dict serializes all fields."""
        task = ReviewTask(
            id="rt-002", intent_id="i-002",
            status=ReviewStatus.ASSIGNED, reviewer="alice",
            resolution="approved",
        )
        d = task.to_dict()
        assert d["id"] == "rt-002"
        assert d["status"] == "assigned"
        assert d["reviewer"] == "alice"
        assert d["resolution"] == "approved"

    def test_from_dict(self, db_path):
        """from_dict deserializes correctly."""
        d = {
            "id": "rt-003", "intent_id": "i-003",
            "status": "escalated", "reviewer": "bob",
            "risk_level": "high", "trigger": "conflict",
        }
        task = ReviewTask.from_dict(d)
        assert task.status == ReviewStatus.ESCALATED
        assert task.reviewer == "bob"
        assert task.risk_level == RiskLevel.HIGH
        assert task.trigger == "conflict"

    def test_from_dict_defaults(self, db_path):
        """from_dict uses defaults for missing fields."""
        d = {"id": "rt-004", "intent_id": "i-004"}
        task = ReviewTask.from_dict(d)
        assert task.status == ReviewStatus.PENDING
        assert task.risk_level == RiskLevel.MEDIUM


class TestReviewTaskPersistence:
    def test_upsert_and_get(self, db_path):
        """Review task persists and retrieves."""
        task = ReviewTask(id="rp-001", intent_id="i-001", reviewer="alice")
        event_log.upsert_review_task(task)
        loaded = event_log.get_review_task("rp-001")
        assert loaded is not None
        assert loaded.id == "rp-001"
        assert loaded.reviewer == "alice"

    def test_get_nonexistent(self, db_path):
        """Nonexistent task returns None."""
        assert event_log.get_review_task("rp-999") is None

    def test_upsert_updates(self, db_path):
        """Upsert updates existing task."""
        task = ReviewTask(id="rp-002", intent_id="i-002", reviewer="alice")
        event_log.upsert_review_task(task)
        task.reviewer = "bob"
        task.status = ReviewStatus.ASSIGNED
        event_log.upsert_review_task(task)
        loaded = event_log.get_review_task("rp-002")
        assert loaded.reviewer == "bob"
        assert loaded.status == ReviewStatus.ASSIGNED

    def test_list_by_status(self, db_path):
        """List filters by status."""
        event_log.upsert_review_task(ReviewTask(
            id="rp-010", intent_id="i-010", status=ReviewStatus.PENDING))
        event_log.upsert_review_task(ReviewTask(
            id="rp-011", intent_id="i-011", status=ReviewStatus.COMPLETED))
        pending = event_log.list_review_tasks(status="pending")
        assert any(t.id == "rp-010" for t in pending)
        assert not any(t.id == "rp-011" for t in pending)

    def test_list_by_reviewer(self, db_path):
        """List filters by reviewer."""
        event_log.upsert_review_task(ReviewTask(
            id="rp-020", intent_id="i-020", reviewer="alice"))
        event_log.upsert_review_task(ReviewTask(
            id="rp-021", intent_id="i-021", reviewer="bob"))
        alice_tasks = event_log.list_review_tasks(reviewer="alice")
        assert any(t.id == "rp-020" for t in alice_tasks)
        assert not any(t.id == "rp-021" for t in alice_tasks)

    def test_list_by_tenant(self, db_path):
        """List filters by tenant."""
        event_log.upsert_review_task(ReviewTask(
            id="rp-030", intent_id="i-030", tenant_id="team-a"))
        event_log.upsert_review_task(ReviewTask(
            id="rp-031", intent_id="i-031", tenant_id="team-b"))
        tasks = event_log.list_review_tasks(tenant_id="team-a")
        assert any(t.id == "rp-030" for t in tasks)
        assert not any(t.id == "rp-031" for t in tasks)

    def test_update_status(self, db_path):
        """update_review_task_status changes status and extra fields."""
        event_log.upsert_review_task(ReviewTask(
            id="rp-040", intent_id="i-040"))
        event_log.update_review_task_status(
        "rp-040", "assigned",
            reviewer="alice", assigned_at=now_iso(),
        )
        loaded = event_log.get_review_task("rp-040")
        assert loaded.status == ReviewStatus.ASSIGNED
        assert loaded.reviewer == "alice"


# ===================================================================
# AR-33: Review task events and lifecycle
# ===================================================================

class TestReviewLifecycle:
    def test_request_creates_task(self, db_path):
        """request_review creates a pending task."""
        make_intent("rl-001")
        task = request_review("rl-001")
        assert task.status == ReviewStatus.PENDING
        assert task.intent_id == "rl-001"
        assert task.sla_deadline is not None

    def test_request_emits_event(self, db_path):
        """request_review emits REVIEW_REQUESTED event."""
        make_intent("rl-010")
        request_review("rl-010")
        events = event_log.query(
        event_type=EventType.REVIEW_REQUESTED,
        )
        assert len(events) >= 1
        assert events[0]["payload"]["intent_id"] == "rl-010"

    def test_request_with_reviewer_auto_assigns(self, db_path):
        """Providing reviewer at creation auto-assigns."""
        make_intent("rl-020")
        task = request_review("rl-020", reviewer="alice")
        assert task.status == ReviewStatus.ASSIGNED
        assert task.reviewer == "alice"
        assert task.assigned_at is not None

    def test_request_with_reviewer_emits_both_events(self, db_path):
        """Auto-assign emits both REQUESTED and ASSIGNED events."""
        make_intent("rl-030")
        request_review("rl-030", reviewer="bob")
        requested = event_log.query(event_type=EventType.REVIEW_REQUESTED)
        assigned = event_log.query(event_type=EventType.REVIEW_ASSIGNED)
        assert len(requested) >= 1
        assert len(assigned) >= 1

    def test_assign_changes_status(self, db_path):
        """assign_review transitions to ASSIGNED."""
        make_intent("rl-040")
        task = request_review("rl-040")
        updated = assign_review(task.id, "alice")
        assert updated.status == ReviewStatus.ASSIGNED
        assert updated.reviewer == "alice"

    def test_reassign_emits_reassigned(self, db_path):
        """Reassigning emits REVIEW_REASSIGNED event."""
        make_intent("rl-050")
        task = request_review("rl-050", reviewer="alice")
        assign_review(task.id, "bob")
        events = event_log.query(
        event_type=EventType.REVIEW_REASSIGNED,
        )
        assert len(events) >= 1
        p = events[0]["payload"]
        assert p["old_reviewer"] == "alice"
        assert p["reviewer"] == "bob"

    def test_complete_with_resolution(self, db_path):
        """complete_review sets resolution and status."""
        make_intent("rl-060")
        task = request_review("rl-060", reviewer="alice")
        completed = complete_review(task.id, resolution="approved", notes="LGTM")
        assert completed.status == ReviewStatus.COMPLETED
        assert completed.resolution == "approved"
        assert completed.notes == "LGTM"
        assert completed.completed_at is not None

    def test_complete_emits_event(self, db_path):
        """complete_review emits REVIEW_COMPLETED event."""
        make_intent("rl-070")
        task = request_review("rl-070", reviewer="alice")
        complete_review(task.id, resolution="rejected")
        events = event_log.query(
        event_type=EventType.REVIEW_COMPLETED,
        )
        assert len(events) >= 1
        assert events[0]["payload"]["resolution"] == "rejected"

    def test_cancel_review(self, db_path):
        """cancel_review transitions to CANCELLED."""
        make_intent("rl-080")
        task = request_review("rl-080")
        cancelled = cancel_review(task.id, reason="no longer needed")
        assert cancelled.status == ReviewStatus.CANCELLED
        events = event_log.query(
        event_type=EventType.REVIEW_CANCELLED,
        )
        assert len(events) >= 1

    def test_escalate_review(self, db_path):
        """escalate_review transitions to ESCALATED."""
        make_intent("rl-090")
        task = request_review("rl-090", reviewer="alice")
        escalated = escalate_review(task.id, reason="sla_breach")
        assert escalated.status == ReviewStatus.ESCALATED
        assert escalated.escalated_at is not None
        events = event_log.query(
        event_type=EventType.REVIEW_ESCALATED,
        )
        assert len(events) >= 1


# ===================================================================
# AR-34: SLA rules and breach detection
# ===================================================================

class TestSLARules:
    def test_sla_deadline_by_risk(self, db_path):
        """SLA deadline differs by risk level."""
        now = now_iso()
        low = _compute_sla_deadline(RiskLevel.LOW, now)
        critical = _compute_sla_deadline(RiskLevel.CRITICAL, now)
        low_dt = datetime.fromisoformat(low)
        critical_dt = datetime.fromisoformat(critical)
        # Critical has shorter SLA than low
        assert critical_dt < low_dt

    def test_sla_hours_match_config(self, db_path):
        """SLA hours match the configured values."""
        assert REVIEW_SLA_HOURS["low"] == 72
        assert REVIEW_SLA_HOURS["critical"] == 8

    def test_request_sets_sla_from_risk(self, db_path):
        """request_review sets SLA based on intent risk."""
        make_intent("sla-001", risk_level=RiskLevel.CRITICAL)
        task = request_review("sla-001")
        # Critical = 8 hours
        deadline = datetime.fromisoformat(task.sla_deadline)
        created = datetime.fromisoformat(task.created_at)
        delta = deadline - created
        assert 7 <= delta.total_seconds() / 3600 <= 9  # ~8 hours

    def test_check_sla_breaches_detects_overdue(self, db_path):
        """check_sla_breaches finds overdue tasks."""
        # Create a task with SLA in the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        task = ReviewTask(
            id="slb-001", intent_id="i-slb",
            status=ReviewStatus.PENDING,
            sla_deadline=past,
            tenant_id="team-a",
        )
        event_log.upsert_review_task(task)
        breaches = check_sla_breaches()
        assert any(b["task_id"] == "slb-001" for b in breaches)

    def test_check_sla_breaches_emits_events(self, db_path):
        """SLA breach detection emits events."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        task = ReviewTask(
            id="slb-010", intent_id="i-slb2",
            status=ReviewStatus.ASSIGNED,
            sla_deadline=past,
        )
        event_log.upsert_review_task(task)
        check_sla_breaches()
        events = event_log.query(
        event_type=EventType.REVIEW_SLA_BREACHED,
        )
        assert any(
            e["payload"].get("task_id") == "slb-010" for e in events
        )

    def test_check_sla_completed_not_breached(self, db_path):
        """Completed tasks are not flagged as breached."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        task = ReviewTask(
            id="slb-020", intent_id="i-slb3",
            status=ReviewStatus.COMPLETED,
            sla_deadline=past,
        )
        event_log.upsert_review_task(task)
        breaches = check_sla_breaches()
        assert not any(b["task_id"] == "slb-020" for b in breaches)

    def test_check_sla_future_deadline_ok(self, db_path):
        """Tasks with future SLA deadline are not breached."""
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        task = ReviewTask(
            id="slb-030", intent_id="i-slb4",
            status=ReviewStatus.PENDING,
            sla_deadline=future,
        )
        event_log.upsert_review_task(task)
        breaches = check_sla_breaches()
        assert not any(b["task_id"] == "slb-030" for b in breaches)


# ===================================================================
# AR-35/36: Summary (for dashboard)
# ===================================================================

class TestReviewSummary:
    def test_summary_counts_by_status(self, db_path):
        """Summary counts tasks by status."""
        event_log.upsert_review_task(ReviewTask(
            id="rs-001", intent_id="i-001", status=ReviewStatus.PENDING))
        event_log.upsert_review_task(ReviewTask(
            id="rs-002", intent_id="i-002", status=ReviewStatus.PENDING))
        event_log.upsert_review_task(ReviewTask(
            id="rs-003", intent_id="i-003", status=ReviewStatus.COMPLETED))
        summary = review_summary()
        assert summary["total"] == 3
        assert summary["by_status"]["pending"] == 2
        assert summary["by_status"]["completed"] == 1

    def test_summary_reviewer_load(self, db_path):
        """Summary tracks reviewer load."""
        event_log.upsert_review_task(ReviewTask(
            id="rs-010", intent_id="i-010",
            status=ReviewStatus.ASSIGNED, reviewer="alice"))
        event_log.upsert_review_task(ReviewTask(
            id="rs-011", intent_id="i-011",
            status=ReviewStatus.ASSIGNED, reviewer="alice"))
        event_log.upsert_review_task(ReviewTask(
            id="rs-012", intent_id="i-012",
            status=ReviewStatus.ASSIGNED, reviewer="bob"))
        summary = review_summary()
        assert summary["by_reviewer"]["alice"] == 2
        assert summary["by_reviewer"]["bob"] == 1

    def test_summary_sla_breached_count(self, db_path):
        """Summary counts SLA-breached tasks."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        event_log.upsert_review_task(ReviewTask(
            id="rs-020", intent_id="i-020",
            status=ReviewStatus.PENDING, sla_deadline=past))
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        event_log.upsert_review_task(ReviewTask(
            id="rs-021", intent_id="i-021",
            status=ReviewStatus.PENDING, sla_deadline=future))
        summary = review_summary()
        assert summary["sla_breached"] == 1

    def test_summary_tenant_filter(self, db_path):
        """Summary respects tenant filter."""
        event_log.upsert_review_task(ReviewTask(
            id="rs-030", intent_id="i-030", tenant_id="team-a"))
        event_log.upsert_review_task(ReviewTask(
            id="rs-031", intent_id="i-031", tenant_id="team-b"))
        summary = review_summary(tenant_id="team-a")
        assert summary["total"] == 1


# ===================================================================
# Round-trip integration
# ===================================================================

class TestReviewRoundTrip:
    def test_full_lifecycle(self, db_path):
        """Full review lifecycle: request → assign → complete."""
        make_intent("rr-001", risk_level=RiskLevel.HIGH)
        task = request_review("rr-001", trigger="policy")
        assert task.status == ReviewStatus.PENDING

        task = assign_review(task.id, "alice")
        assert task.status == ReviewStatus.ASSIGNED

        task = complete_review(task.id, resolution="approved")
        assert task.status == ReviewStatus.COMPLETED
        assert task.resolution == "approved"

        # Verify persisted
        loaded = event_log.get_review_task(task.id)
        assert loaded.status == ReviewStatus.COMPLETED

    def test_escalation_lifecycle(self, db_path):
        """Review lifecycle with escalation: request → assign → escalate."""
        make_intent("rr-010", risk_level=RiskLevel.CRITICAL)
        task = request_review("rr-010", reviewer="alice")
        task = escalate_review(task.id, reason="sla_breach")
        assert task.status == ReviewStatus.ESCALATED

        # Can still be completed after escalation
        task = complete_review(task.id, resolution="deferred")
        assert task.status == ReviewStatus.COMPLETED
        assert task.resolution == "deferred"

    def test_request_nonexistent_intent_raises(self, db_path):
        """Requesting review for nonexistent intent raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="not found"):
            request_review("nonexistent-001")
