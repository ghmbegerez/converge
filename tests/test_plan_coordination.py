"""Tests for plan_id field and dependency enforcement in queue processing (AR-47)."""

from conftest import make_intent

from converge import engine, event_log
from converge.models import Event, EventType, Intent, RiskLevel, Status


class TestPlanIdField:
    """plan_id is persisted and round-trips through the store."""

    def test_plan_id_persisted(self, db_path):
        make_intent(id="p-001", plan_id="plan-alpha")
        loaded = event_log.get_intent("p-001")
        assert loaded.plan_id == "plan-alpha"

    def test_plan_id_null_by_default(self, db_path):
        make_intent(id="p-002")
        loaded = event_log.get_intent("p-002")
        assert loaded.plan_id is None

    def test_plan_id_in_to_dict(self, db_path):
        intent = Intent(
            id="p-003", source="f/x", target="main",
            status=Status.READY, plan_id="plan-beta",
        )
        d = intent.to_dict()
        assert d["plan_id"] == "plan-beta"

    def test_plan_id_from_dict(self, db_path):
        d = {"id": "p-004", "source": "f/x", "target": "main", "plan_id": "plan-gamma"}
        intent = Intent.from_dict(d)
        assert intent.plan_id == "plan-gamma"

    def test_plan_id_from_dict_missing(self, db_path):
        d = {"id": "p-005", "source": "f/x", "target": "main"}
        intent = Intent.from_dict(d)
        assert intent.plan_id is None


class TestDependencyEnforcement:
    """process_queue skips intents whose dependencies are not all MERGED."""

    def test_no_dependencies_processes_normally(self, db_path):
        """Intents without dependencies are processed as before."""
        make_intent(id="d-001", status=Status.VALIDATED, dependencies=[])

        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        assert len(results) == 1
        assert results[0]["intent_id"] == "d-001"
        # Should not be dependency_blocked
        assert results[0]["decision"] != "dependency_blocked"

    def test_all_deps_merged_processes(self, db_path):
        """Intent proceeds when all dependencies are MERGED."""
        make_intent(id="dep-a", status=Status.MERGED)
        make_intent(id="dep-b", status=Status.MERGED)
        make_intent(
            id="d-002", status=Status.VALIDATED,
            dependencies=["dep-a", "dep-b"], plan_id="plan-x",
        )

        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        assert len(results) == 1
        assert results[0]["intent_id"] == "d-002"
        assert results[0]["decision"] != "dependency_blocked"

    def test_unmet_dep_blocks_intent(self, db_path):
        """Intent is skipped when a dependency is not MERGED."""
        make_intent(id="dep-c", status=Status.VALIDATED)
        make_intent(
            id="d-003", status=Status.VALIDATED,
            dependencies=["dep-c"], plan_id="plan-y",
        )

        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        # d-003 should be dependency_blocked, dep-c should process normally
        blocked = [r for r in results if r.get("decision") == "dependency_blocked"]
        assert len(blocked) == 1
        assert blocked[0]["intent_id"] == "d-003"
        assert blocked[0]["unmet_dependencies"] == ["dep-c"]
        assert blocked[0]["plan_id"] == "plan-y"

    def test_missing_dep_blocks_intent(self, db_path):
        """Intent is skipped when a dependency does not exist."""
        make_intent(
            id="d-004", status=Status.VALIDATED,
            dependencies=["nonexistent-dep"],
        )

        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        assert len(results) == 1
        assert results[0]["decision"] == "dependency_blocked"
        assert "nonexistent-dep" in results[0]["unmet_dependencies"]

    def test_partial_deps_met_blocks(self, db_path):
        """Intent is blocked if even one dependency is not MERGED."""
        make_intent(id="dep-ok", status=Status.MERGED)
        make_intent(id="dep-pending", status=Status.VALIDATED)
        make_intent(
            id="d-005", status=Status.VALIDATED,
            dependencies=["dep-ok", "dep-pending"], plan_id="plan-z",
        )

        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        blocked = [r for r in results if r["intent_id"] == "d-005"]
        assert len(blocked) == 1
        assert blocked[0]["decision"] == "dependency_blocked"
        assert blocked[0]["unmet_dependencies"] == ["dep-pending"]

    def test_dependency_blocked_emits_event(self, db_path):
        """Blocking emits an INTENT_DEPENDENCY_BLOCKED event."""
        make_intent(id="dep-e", status=Status.READY)
        make_intent(
            id="d-006", status=Status.VALIDATED,
            dependencies=["dep-e"], plan_id="plan-ev",
        )

        engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )

        events = event_log.query(event_type=EventType.INTENT_DEPENDENCY_BLOCKED)
        assert len(events) >= 1
        ev = events[0]
        assert ev["intent_id"] == "d-006"
        assert ev["payload"]["unmet_dependencies"] == ["dep-e"]
        assert ev["payload"]["plan_id"] == "plan-ev"

    def test_dependency_blocked_does_not_change_status(self, db_path):
        """Blocked intent stays VALIDATED — it will be retried next cycle."""
        make_intent(id="dep-f", status=Status.READY)
        make_intent(
            id="d-007", status=Status.VALIDATED,
            dependencies=["dep-f"],
        )

        engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )

        loaded = event_log.get_intent("d-007")
        assert loaded.status == Status.VALIDATED


class TestPlanCoordinationE2E:
    """End-to-end: a plan with 3 intents processes in dependency order."""

    def test_plan_processes_in_order(self, db_path):
        """Only the intent with no unmet deps processes each cycle."""
        # Plan: i1 (no deps) -> i2 (deps=[i1]) -> i3 (deps=[i2])
        make_intent(
            id="i1", status=Status.VALIDATED,
            dependencies=[], plan_id="plan-ordered",
        )
        make_intent(
            id="i2", status=Status.VALIDATED,
            dependencies=["i1"], plan_id="plan-ordered",
        )
        make_intent(
            id="i3", status=Status.VALIDATED,
            dependencies=["i2"], plan_id="plan-ordered",
        )

        # Cycle 1: only i1 should process (i2 and i3 blocked)
        results = engine.process_queue(
        use_last_simulation=True, skip_checks=True,
        )
        decisions = {r["intent_id"]: r["decision"] for r in results}
        assert decisions["i1"] != "dependency_blocked"
        assert decisions["i2"] == "dependency_blocked"
        assert decisions["i3"] == "dependency_blocked"
