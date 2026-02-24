"""Explicit invariant tests for architectural contracts.

These tests enforce invariants that are implicit in the code structure but
critical for correctness.  If an invariant breaks, the test names explain
*what* invariant was violated, not just *what* failed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from converge.models import (
    AgentPolicy,
    CheckResult,
    CommitLink,
    Event,
    GateResult,
    Intent,
    PolicyEvaluation,
    ReviewTask,
    RiskEval,
    Simulation,
    Status,
)

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "converge"


# ===========================================================================
# Invariant 1: Engine decision invariants
# ===========================================================================

class TestMergeableInvariant:
    """Invariant 1: mergeable(i, t) = can_merge(M(t), Δi) ∧ checks_pass.

    If simulation shows conflicts, validate_intent MUST block.
    """

    def test_conflicts_cause_block(self, db_path):
        from converge import engine, event_log

        intent = Intent(
            id="inv1-conflict", source="feature/x", target="main",
            status=Status.READY,
        )
        event_log.upsert_intent(intent)

        conflict_sim = Simulation(
            mergeable=False, conflicts=["file.py"], source="feature/x", target="main",
        )
        result = engine.validate_intent(intent, sim=conflict_sim)
        assert result["decision"] == "blocked"
        assert "conflict" in result["reason"].lower()

    def test_clean_merge_validates(self, db_path):
        from converge import engine, event_log

        intent = Intent(
            id="inv1-clean", source="feature/x", target="main",
            status=Status.READY,
        )
        event_log.upsert_intent(intent)

        clean_sim = Simulation(
            mergeable=True, files_changed=["a.py"], source="feature/x", target="main",
        )
        result = engine.validate_intent(intent, sim=clean_sim, skip_checks=True)
        assert result["decision"] == "validated"


class TestMaxRetriesInvariant:
    """Invariant 3: retries >= max_retries → REJECTED.

    An intent that has exhausted its retry budget MUST be rejected.
    """

    def test_exhausted_retries_rejected(self, db_path):
        from converge import engine, event_log

        intent = Intent(
            id="inv3-retries", source="feature/x", target="main",
            status=Status.VALIDATED, retries=5,
        )
        event_log.upsert_intent(intent)

        results = engine.process_queue(
            max_retries=3, skip_checks=True, use_last_simulation=True,
        )
        rejected = [r for r in results if r.get("decision") == "rejected"]
        assert len(rejected) == 1
        assert rejected[0]["intent_id"] == "inv3-retries"


class TestMergeFailureInvariant:
    """Invariant 4: Failed merge MUST NOT set MERGED status.

    When scm.execute_merge() raises an exception, the intent must
    remain actionable (READY for retry or REJECTED if max retries exceeded),
    never MERGED.
    """

    @staticmethod
    def _setup_validated_intent(event_log, intent_id):
        """Create a VALIDATED intent with a previous simulation so process_queue can reach merge."""
        from converge.event_types import EventType as ET
        intent = Intent(
            id=intent_id, source="feature/x", target="main",
            status=Status.VALIDATED, retries=0,
        )
        event_log.upsert_intent(intent)
        event_log.append(Event(
            event_type=ET.SIMULATION_COMPLETED,
            intent_id=intent_id,
            payload={"mergeable": True, "conflicts": [], "files_changed": ["a.py"],
                     "source": "feature/x", "target": "main"},
        ))
        return intent

    def test_failed_merge_must_not_set_merged(self, db_path):
        from unittest.mock import patch
        from converge import engine, event_log

        self._setup_validated_intent(event_log, "inv4-merge-fail")

        with patch("converge.scm.execute_merge", side_effect=RuntimeError("git merge failed")):
            engine.process_queue(
                max_retries=3, skip_checks=True, use_last_simulation=True,
                auto_confirm=True,
            )

        updated = event_log.get_intent("inv4-merge-fail")
        assert updated.status != Status.MERGED, \
            f"Failed merge set status to {updated.status.value}, expected READY or REJECTED"
        assert updated.status == Status.READY
        assert updated.retries == 1

    def test_failed_merge_emits_merge_failed_event(self, db_path):
        from unittest.mock import patch
        from converge import engine, event_log
        from converge.event_types import EventType

        self._setup_validated_intent(event_log, "inv4-merge-evt")

        with patch("converge.scm.execute_merge", side_effect=RuntimeError("boom")):
            engine.process_queue(
                max_retries=3, skip_checks=True, use_last_simulation=True,
                auto_confirm=True,
            )

        events = event_log.query(event_type=EventType.INTENT_MERGE_FAILED)
        assert len(events) >= 1
        assert events[0]["intent_id"] == "inv4-merge-evt"

    def test_no_simulated_sha_in_engine(self):
        """The fake SHA pattern 'simulated-' must not appear in engine.py."""
        engine_src = (SRC_DIR / "engine.py").read_text()
        assert "simulated-" not in engine_src, "Found fake SHA pattern in engine.py"


# ===========================================================================
# Invariant 2: Serialization contract
# ===========================================================================

class TestSerializationContract:
    """Persistent models MUST have to_dict().  Round-trippable models MUST
    also have from_dict()."""

    PERSISTENT_MODELS_WITH_ROUNDTRIP = [Intent, ReviewTask, AgentPolicy]
    PERSISTENT_MODELS_WITH_TO_DICT = [Event, RiskEval, CommitLink]

    def test_persistent_models_have_to_dict(self):
        for cls in self.PERSISTENT_MODELS_WITH_ROUNDTRIP + self.PERSISTENT_MODELS_WITH_TO_DICT:
            assert hasattr(cls, "to_dict"), f"{cls.__name__} missing to_dict()"

    def test_roundtrip_models_have_from_dict(self):
        for cls in self.PERSISTENT_MODELS_WITH_ROUNDTRIP:
            assert hasattr(cls, "from_dict"), f"{cls.__name__} missing from_dict()"

    def test_intent_roundtrip(self):
        original = Intent(id="rt-1", source="a", target="b", status=Status.READY)
        restored = Intent.from_dict(original.to_dict())
        assert original.id == restored.id
        assert original.status == restored.status
        assert original.risk_level == restored.risk_level

    def test_review_task_roundtrip(self):
        original = ReviewTask(id="rt-2", intent_id="i-1")
        restored = ReviewTask.from_dict(original.to_dict())
        assert original.id == restored.id
        assert original.status == restored.status


# ===========================================================================
# Invariant 3: EventType naming convention
# ===========================================================================

class TestEventTypeNaming:
    """EventType values MUST follow the ``domain.action`` or
    ``domain.sub.action`` format (lowercase, dot-separated)."""

    EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$")

    def test_all_event_types_follow_naming_convention(self):
        from converge.event_types import EventType
        violations = []
        for attr in dir(EventType):
            if attr.startswith("_"):
                continue
            value = getattr(EventType, attr)
            if not isinstance(value, str):
                continue
            if not self.EVENT_TYPE_PATTERN.match(value):
                violations.append(f"{attr} = {value!r}")
        assert violations == [], f"EventType naming violations: {violations}"

    def test_event_type_count_bounded(self):
        """Guard against unbounded growth of event types."""
        from converge.event_types import EventType
        count = sum(1 for attr in dir(EventType) if not attr.startswith("_"))
        assert count < 80, f"EventType has {count} entries — review if growth is justified"


# ===========================================================================
# Invariant 4: Enum consistency
# ===========================================================================

class TestEnumConsistency:
    """Source code MUST NOT compare enum fields using string literals
    when the enum is available.  The pattern ``x.value in ("string", ...)``
    should be ``x in (Enum.X, ...)`` instead."""

    def test_no_status_value_string_comparison(self):
        """No .status.value in ("READY", ...) patterns in source."""
        violations = self._scan_pattern(
            r'\.status\.value\s+in\s+\(', SRC_DIR,
        )
        assert violations == [], f"Use enum comparison instead of .value: {violations}"

    def test_no_severity_value_string_comparison(self):
        """No .severity.value in ("critical", ...) patterns in source."""
        violations = self._scan_pattern(
            r'\.severity\.value\s+in\s+\(', SRC_DIR,
        )
        assert violations == [], f"Use enum comparison instead of .value: {violations}"

    @staticmethod
    def _scan_pattern(pattern: str, directory: Path) -> list[str]:
        regex = re.compile(pattern)
        violations = []
        for py_file in sorted(directory.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            for i, line in enumerate(py_file.read_text().splitlines(), 1):
                if regex.search(line):
                    rel = py_file.relative_to(directory)
                    violations.append(f"{rel}:{i}: {line.strip()}")
        return violations


# ===========================================================================
# Invariant 5: Constants single source of truth
# ===========================================================================

class TestConstantsCentralized:
    """Shared constants MUST be defined in defaults.py, not scattered."""

    def test_no_hardcoded_large_limits_outside_defaults(self):
        """No `limit=10000` or `limit=10_000` outside defaults.py."""
        violations = []
        for py_file in sorted(SRC_DIR.rglob("*.py")):
            if "__pycache__" in str(py_file) or py_file.name == "defaults.py":
                continue
            for i, line in enumerate(py_file.read_text().splitlines(), 1):
                if re.search(r'limit\s*=\s*1[0_]000\b', line):
                    rel = py_file.relative_to(SRC_DIR)
                    violations.append(f"{rel}:{i}: {line.strip()}")
        assert violations == [], f"Hardcoded limits found: {violations}"

    def test_defaults_module_exists(self):
        assert (SRC_DIR / "defaults.py").exists(), "defaults.py must exist"

    def test_defaults_exports_key_constants(self):
        from converge.defaults import (
            QUERY_LIMIT_LARGE,
            QUERY_LIMIT_MEDIUM,
            QUERY_LIMIT_UNBOUNDED,
            MAX_RETRIES,
            REVIEW_SLA_HOURS,
            ROLLOUT_HASH_CHARS,
        )
        assert QUERY_LIMIT_LARGE == 10_000
        assert QUERY_LIMIT_MEDIUM == 500
        assert QUERY_LIMIT_UNBOUNDED == 100_000
        assert MAX_RETRIES == 3
        assert "critical" in REVIEW_SLA_HOURS
        assert ROLLOUT_HASH_CHARS == 8


# ===========================================================================
# Invariant 6: Store helpers used consistently
# ===========================================================================

class TestStoreHelpers:
    """base_store.py methods MUST use _build_where for filtering."""

    def test_build_where_exists(self):
        from converge.adapters.base_store import BaseConvergeStore
        assert hasattr(BaseConvergeStore, "_build_where")

    def test_upsert_policy_exists(self):
        from converge.adapters.base_store import BaseConvergeStore
        assert hasattr(BaseConvergeStore, "_upsert_policy")


# ===========================================================================
# Invariant 7: Facade completeness
# ===========================================================================

class TestFacadeCompleteness:
    """event_log facade MUST expose all port methods."""

    def test_facade_covers_all_store_methods(self):
        from converge import event_log
        from converge.adapters.base_store import BaseConvergeStore

        # Public methods on the store (exclude ABC internals and helpers)
        store_methods = {
            m for m in dir(BaseConvergeStore)
            if not m.startswith("_") and callable(getattr(BaseConvergeStore, m))
            and m != "close"
        }

        facade_functions = {
            m for m in dir(event_log)
            if not m.startswith("_") and callable(getattr(event_log, m))
        }

        missing = store_methods - facade_functions
        # Allow some methods that don't need facade exposure
        allowed_missing = {"close"}
        actual_missing = missing - allowed_missing
        assert actual_missing == set(), f"Store methods missing from facade: {actual_missing}"
