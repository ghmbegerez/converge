"""Tests for the event log (source of truth)."""

from converge import event_log
from converge.models import Event, Intent, RiskLevel, Status, now_iso


def test_append_and_query(db_path):
    event = Event(
        event_type="simulation.completed",
        intent_id="int-001",
        tenant_id="team-a",
        payload={"mergeable": True, "files_changed": ["a.py"]},
    )
    event_log.append(db_path, event)

    results = event_log.query(db_path, event_type="simulation.completed")
    assert len(results) == 1
    assert results[0]["payload"]["mergeable"] is True
    assert results[0]["intent_id"] == "int-001"


def test_query_filters(db_path):
    for i in range(5):
        event_log.append(db_path, Event(
            event_type="simulation.completed" if i < 3 else "check.completed",
            intent_id=f"int-{i:03d}",
            tenant_id="team-a" if i < 4 else "team-b",
            payload={"i": i},
        ))

    assert len(event_log.query(db_path, event_type="simulation.completed")) == 3
    assert len(event_log.query(db_path, tenant_id="team-a")) == 4
    assert len(event_log.query(db_path, intent_id="int-002")) == 1


def test_count(db_path):
    for i in range(3):
        event_log.append(db_path, Event(event_type="test.event", payload={"i": i}))
    assert event_log.count(db_path, event_type="test.event") == 3
    assert event_log.count(db_path, event_type="other") == 0


def test_count_rejects_invalid_filter(db_path):
    """count() rejects filter keys not in the whitelist (SQL injection prevention)."""
    import pytest
    with pytest.raises(ValueError, match="Invalid filter column"):
        event_log.count(db_path, **{"1=1; DROP TABLE events--": "x"})


def test_count_allows_valid_filters(db_path):
    """count() accepts all valid filter columns."""
    event_log.append(db_path, Event(event_type="test.count", intent_id="i-1",
                                     tenant_id="t-1", payload={}))
    assert event_log.count(db_path, event_type="test.count") == 1
    assert event_log.count(db_path, intent_id="i-1") == 1
    assert event_log.count(db_path, tenant_id="t-1") == 1
    assert event_log.count(db_path, event_type="test.count", tenant_id="t-1") == 1


def test_intent_crud(db_path, sample_intent):
    event_log.upsert_intent(db_path, sample_intent)

    loaded = event_log.get_intent(db_path, "test-001")
    assert loaded is not None
    assert loaded.source == "feature/login"
    assert loaded.status == Status.READY
    assert loaded.risk_level == RiskLevel.MEDIUM
    assert loaded.tenant_id == "team-a"

    event_log.update_intent_status(db_path, "test-001", Status.VALIDATED)
    loaded = event_log.get_intent(db_path, "test-001")
    assert loaded.status == Status.VALIDATED

    event_log.update_intent_status(db_path, "test-001", Status.REJECTED, retries=3)
    loaded = event_log.get_intent(db_path, "test-001")
    assert loaded.retries == 3


def test_list_intents_ordering(db_path):
    for i, (prio, name) in enumerate([(3, "c"), (1, "a"), (2, "b")]):
        intent = Intent(id=name, source=f"f/{name}", target="main",
                        status=Status.READY, priority=prio)
        event_log.upsert_intent(db_path, intent)

    intents = event_log.list_intents(db_path)
    ids = [i.id for i in intents]
    assert ids == ["a", "b", "c"]  # sorted by priority


def test_prune_events(db_path):
    old = Event(event_type="old.event", payload={}, timestamp="2020-01-01T00:00:00+00:00")
    new = Event(event_type="new.event", payload={}, timestamp=now_iso())
    event_log.append(db_path, old)
    event_log.append(db_path, new)

    pruned = event_log.prune_events(db_path, "2023-01-01T00:00:00+00:00", dry_run=True)
    assert pruned == 1
    assert event_log.count(db_path) == 2  # dry_run: nothing deleted

    pruned = event_log.prune_events(db_path, "2023-01-01T00:00:00+00:00")
    assert pruned == 1
    assert event_log.count(db_path) == 1


def test_agent_policy_storage(db_path):
    data = {"agent_id": "bot-1", "tenant_id": "team-a", "atl": 2, "allow_actions": ["analyze", "merge"]}
    event_log.upsert_agent_policy(db_path, data)

    loaded = event_log.get_agent_policy(db_path, "bot-1", "team-a")
    assert loaded is not None
    assert loaded["atl"] == 2

    all_policies = event_log.list_agent_policies(db_path)
    assert len(all_policies) == 1


def test_risk_policy_storage(db_path):
    data = {"max_risk_score": 50.0, "mode": "enforce"}
    event_log.upsert_risk_policy(db_path, "team-a", data)

    loaded = event_log.get_risk_policy(db_path, "team-a")
    assert loaded is not None
    assert loaded["max_risk_score"] == 50.0
    assert loaded["version"] == 1

    # Update increments version
    event_log.upsert_risk_policy(db_path, "team-a", {"max_risk_score": 45.0})
    loaded = event_log.get_risk_policy(db_path, "team-a")
    assert loaded["version"] == 2


# ---------------------------------------------------------------------------
# Queue lock tests
# ---------------------------------------------------------------------------

def test_queue_lock_acquire_release(db_path):
    """Basic acquire and release cycle."""
    assert event_log.acquire_queue_lock(db_path, holder_pid=1000)
    # Second acquire fails (held by pid 1000)
    assert not event_log.acquire_queue_lock(db_path, holder_pid=2000)
    # Release by holder succeeds
    assert event_log.release_queue_lock(db_path, holder_pid=1000)
    # Now another can acquire
    assert event_log.acquire_queue_lock(db_path, holder_pid=2000)
    event_log.release_queue_lock(db_path, holder_pid=2000)


def test_queue_lock_info(db_path):
    """Lock info is retrievable."""
    assert event_log.get_queue_lock_info(db_path) is None
    event_log.acquire_queue_lock(db_path, holder_pid=42)
    info = event_log.get_queue_lock_info(db_path)
    assert info is not None
    assert info["holder_pid"] == 42
    assert info["lock_name"] == "queue"
    event_log.release_queue_lock(db_path, holder_pid=42)


def test_queue_lock_force_release(db_path):
    """Force release works regardless of holder."""
    event_log.acquire_queue_lock(db_path, holder_pid=1000)
    # Can't release with wrong pid
    assert not event_log.release_queue_lock(db_path, holder_pid=9999)
    # Force release works
    assert event_log.force_release_queue_lock(db_path)
    assert event_log.get_queue_lock_info(db_path) is None


def test_queue_lock_expiry(db_path):
    """Expired locks get reclaimed."""
    # Acquire with TTL=0 (instantly expired)
    event_log.acquire_queue_lock(db_path, holder_pid=1000, ttl_seconds=0)
    import time
    time.sleep(0.01)  # ensure time passes
    # Another process can now reclaim because it's expired
    assert event_log.acquire_queue_lock(db_path, holder_pid=2000, ttl_seconds=300)
    info = event_log.get_queue_lock_info(db_path)
    assert info["holder_pid"] == 2000
    event_log.release_queue_lock(db_path, holder_pid=2000)


class TestTenantIsolationInLists:
    """list_* functions filter by tenant_id when provided."""

    def test_list_risk_policies_filtered(self, db_path):
        event_log.upsert_risk_policy(db_path, "team-a", {"score": 1})
        event_log.upsert_risk_policy(db_path, "team-b", {"score": 2})

        all_policies = event_log.list_risk_policies(db_path)
        assert len(all_policies) == 2

        filtered = event_log.list_risk_policies(db_path, tenant_id="team-a")
        assert len(filtered) == 1
        assert filtered[0]["tenant_id"] == "team-a"

    def test_list_agent_policies_filtered(self, db_path):
        event_log.upsert_agent_policy(db_path, {"agent_id": "bot-1", "tenant_id": "team-a"})
        event_log.upsert_agent_policy(db_path, {"agent_id": "bot-2", "tenant_id": "team-b"})

        all_policies = event_log.list_agent_policies(db_path)
        assert len(all_policies) == 2

        filtered = event_log.list_agent_policies(db_path, tenant_id="team-a")
        assert len(filtered) == 1

    def test_list_compliance_thresholds_filtered(self, db_path):
        event_log.upsert_compliance_thresholds(db_path, "team-a", {"slo": 0.8})
        event_log.upsert_compliance_thresholds(db_path, "team-b", {"slo": 0.9})

        all_thresholds = event_log.list_compliance_thresholds(db_path)
        assert len(all_thresholds) == 2

        filtered = event_log.list_compliance_thresholds(db_path, tenant_id="team-a")
        assert len(filtered) == 1
        assert filtered[0]["tenant_id"] == "team-a"
