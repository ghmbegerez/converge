"""Contract tests for ConvergeStore implementations.

Every storage backend must pass these tests.  The ``contract_store`` fixture
is parametrised so that adding a new backend only requires extending the
params list.  Postgres tests require ``CONVERGE_TEST_PG_DSN`` to be set;
they are skipped otherwise.
"""

from __future__ import annotations

import os

import pytest

from converge.adapters.sqlite_store import SqliteStore
from converge.models import Event, Intent, RiskLevel, Status, new_id, now_iso
from converge.ports import (
    ConvergeStore,
    DeliveryPort,
    EventStorePort,
    IntentStorePort,
    LockPort,
    PolicyStorePort,
)


# ---------------------------------------------------------------------------
# Parametrised fixture — extend params for new backends
# ---------------------------------------------------------------------------

def _pg_available() -> bool:
    return bool(os.environ.get("CONVERGE_TEST_PG_DSN"))


_backends = ["sqlite"]
if _pg_available():
    _backends.append("postgres")


@pytest.fixture(params=_backends)
def contract_store(request, tmp_path):
    if request.param == "sqlite":
        store = SqliteStore(tmp_path / "contract.db")
        yield store
        store.close()
    elif request.param == "postgres":
        from converge.adapters.postgres_store import PostgresStore

        dsn = os.environ["CONVERGE_TEST_PG_DSN"]
        store = PostgresStore(dsn, min_size=1, max_size=2)
        # Clean tables before each test for isolation
        import psycopg
        with psycopg.connect(dsn) as conn:
            for table in (
                "webhook_deliveries", "queue_locks", "risk_policies",
                "compliance_thresholds", "agent_policies", "intents", "events",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        yield store
        store.close()
    else:
        raise ValueError(f"Unknown backend: {request.param}")


# ===================================================================
# Protocol conformance
# ===================================================================

class TestProtocolConformance:
    def test_is_event_store(self, contract_store):
        assert isinstance(contract_store, EventStorePort)

    def test_is_intent_store(self, contract_store):
        assert isinstance(contract_store, IntentStorePort)

    def test_is_policy_store(self, contract_store):
        assert isinstance(contract_store, PolicyStorePort)

    def test_is_lock_port(self, contract_store):
        assert isinstance(contract_store, LockPort)

    def test_is_delivery_port(self, contract_store):
        assert isinstance(contract_store, DeliveryPort)


# ===================================================================
# EventStorePort contract
# ===================================================================

class TestEventStoreContract:
    def test_append_and_query(self, contract_store):
        ev = Event(event_type="test.created", payload={"key": "value"}, trace_id="t-1")
        result = contract_store.append(ev)
        assert result.id == ev.id

        rows = contract_store.query(event_type="test.created")
        assert len(rows) == 1
        assert rows[0]["payload"]["key"] == "value"

    def test_query_filters(self, contract_store):
        contract_store.append(Event(event_type="a", payload={}, intent_id="i1", trace_id="t"))
        contract_store.append(Event(event_type="b", payload={}, intent_id="i2", trace_id="t"))

        assert len(contract_store.query(intent_id="i1")) == 1
        assert len(contract_store.query(event_type="b")) == 1
        assert len(contract_store.query()) == 2

    def test_count(self, contract_store):
        contract_store.append(Event(event_type="x", payload={}, trace_id="t"))
        contract_store.append(Event(event_type="x", payload={}, trace_id="t"))
        contract_store.append(Event(event_type="y", payload={}, trace_id="t"))

        assert contract_store.count() == 3
        assert contract_store.count(event_type="x") == 2

    def test_count_rejects_invalid_filter(self, contract_store):
        with pytest.raises(ValueError, match="Invalid filter column"):
            contract_store.count(bad_column="oops")

    def test_prune(self, contract_store):
        old_ts = "2020-01-01T00:00:00+00:00"
        contract_store.append(Event(event_type="old", payload={}, trace_id="t", timestamp=old_ts))
        contract_store.append(Event(event_type="new", payload={}, trace_id="t"))

        pruned = contract_store.prune_events(before="2024-01-01T00:00:00+00:00")
        assert pruned == 1
        assert contract_store.count() == 1


# ===================================================================
# IntentStorePort contract
# ===================================================================

class TestIntentStoreContract:
    def _make_intent(self, id_: str, priority: int = 3, status: Status = Status.READY) -> Intent:
        return Intent(id=id_, source="f/a", target="main", status=status, priority=priority)

    def test_upsert_and_get(self, contract_store):
        intent = self._make_intent("i-1")
        contract_store.upsert_intent(intent)
        got = contract_store.get_intent("i-1")
        assert got is not None
        assert got.id == "i-1"
        assert got.status == Status.READY

    def test_list_ordering(self, contract_store):
        contract_store.upsert_intent(self._make_intent("low", priority=5))
        contract_store.upsert_intent(self._make_intent("high", priority=1))
        intents = contract_store.list_intents()
        assert intents[0].id == "high"

    def test_update_status(self, contract_store):
        contract_store.upsert_intent(self._make_intent("i-1"))
        contract_store.update_intent_status("i-1", Status.MERGED)
        got = contract_store.get_intent("i-1")
        assert got is not None
        assert got.status == Status.MERGED


# ===================================================================
# PolicyStorePort contract
# ===================================================================

class TestPolicyStoreContract:
    def test_agent_policy_crud(self, contract_store):
        data = {"agent_id": "bot-1", "atl": 2}
        contract_store.upsert_agent_policy(data)
        got = contract_store.get_agent_policy("bot-1")
        assert got is not None
        assert got["atl"] == 2

        policies = contract_store.list_agent_policies()
        assert len(policies) == 1

    def test_risk_policy_versioning(self, contract_store):
        contract_store.upsert_risk_policy("t1", {"max_score": 10})
        v1 = contract_store.get_risk_policy("t1")
        assert v1 is not None
        assert v1["version"] == 1

        contract_store.upsert_risk_policy("t1", {"max_score": 20})
        v2 = contract_store.get_risk_policy("t1")
        assert v2 is not None
        assert v2["version"] == 2

    def test_compliance_thresholds(self, contract_store):
        contract_store.upsert_compliance_thresholds("t1", {"mergeable_rate": 0.9})
        got = contract_store.get_compliance_thresholds("t1")
        assert got is not None
        assert got["mergeable_rate"] == 0.9

        all_ct = contract_store.list_compliance_thresholds()
        assert len(all_ct) == 1


# ===================================================================
# LockPort contract
# ===================================================================

class TestLockContract:
    def test_acquire_and_release(self, contract_store):
        pid = os.getpid()
        assert contract_store.acquire_queue_lock(holder_pid=pid) is True
        # same pid can't acquire again
        assert contract_store.acquire_queue_lock(holder_pid=pid) is False
        assert contract_store.release_queue_lock(holder_pid=pid) is True

    def test_force_release(self, contract_store):
        contract_store.acquire_queue_lock(holder_pid=99999)
        assert contract_store.force_release_queue_lock() is True
        # Now we can acquire
        assert contract_store.acquire_queue_lock() is True


# ===================================================================
# DeliveryPort contract
# ===================================================================

class TestDeliveryContract:
    def test_record_and_check_duplicate(self, contract_store):
        assert contract_store.is_duplicate_delivery("d-1") is False
        contract_store.record_delivery("d-1")
        assert contract_store.is_duplicate_delivery("d-1") is True
