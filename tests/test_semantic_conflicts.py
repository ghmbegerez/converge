"""Tests for semantic conflict detection (AR-18..AR-21).

The indexer now uses build_semantic_text() (which excludes intent ID and plan ID)
for embedding generation.  Two intents with identical semantic content will produce
identical vectors via the deterministic provider — no manual vector injection needed.

Legacy tests that inject vectors directly via upsert_embedding still work.
"""

import json
import math

from conftest import make_intent

from converge import event_log
from converge.models import Event, EventType, Intent, RiskLevel, Status, now_iso
from converge.semantic.conflicts import (
    ConflictCandidate,
    _cosine_similarity,
    _scope_overlap,
    _target_overlap,
    generate_candidates,
    list_conflicts,
    resolve_conflict,
    scan_conflicts,
    score_conflict,
)
from converge.semantic.embeddings import get_provider
from converge.semantic.indexer import index_intent


def _index(intent_id, provider=None):
    """Index a single intent."""
    if provider is None:
        provider = get_provider("deterministic")
    return index_intent(intent_id, provider)


def _set_identical_vectors(ids, model="deterministic-v1", dim=64):
    """Set identical embedding vectors for a list of intent IDs."""
    # Fixed unit vector: [1/sqrt(dim), 1/sqrt(dim), ...]
    val = 1.0 / math.sqrt(dim)
    vec = [val] * dim
    vec_json = json.dumps(vec)
    for iid in ids:
        event_log.upsert_embedding(
        iid, model, dim, "fixed-checksum", vec_json, now_iso(),
        )


# ===================================================================
# AR-18: Cosine similarity and candidate generation
# ===================================================================

class TestCosineSimilarity:
    def test_identical_vectors(self):
        """Identical vectors have similarity 1.0."""
        v = [0.5, 0.3, 0.8, 0.1]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Orthogonal vectors have similarity 0.0."""
        assert abs(_cosine_similarity([1, 0], [0, 1])) < 1e-6

    def test_opposite_vectors(self):
        """Opposite vectors have similarity -1.0."""
        assert abs(_cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6

    def test_empty_vectors(self):
        """Empty vectors return 0.0."""
        assert _cosine_similarity([], []) == 0.0

    def test_mismatched_length(self):
        """Mismatched lengths return 0.0."""
        assert _cosine_similarity([1, 2], [1, 2, 3]) == 0.0

    def test_zero_vector(self):
        """Zero vector returns 0.0."""
        assert _cosine_similarity([0, 0], [1, 1]) == 0.0


class TestCandidateGeneration:
    def test_no_intents(self, db_path):
        """No intents → no candidates."""
        candidates = generate_candidates()
        assert candidates == []

    def test_single_intent(self, db_path):
        """Single intent → no candidates."""
        make_intent("c-001")
        _index("c-001")
        candidates = generate_candidates()
        assert candidates == []

    def test_same_plan_excluded(self, db_path):
        """Intents with same plan_id are excluded from comparison."""
        make_intent("c-010", plan_id="plan-A")
        make_intent("c-011", plan_id="plan-A")
        _set_identical_vectors(["c-010", "c-011"])
        candidates = generate_candidates(similarity_threshold=0.5)
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in candidates}
        assert ("c-010", "c-011") not in pair_ids

    def test_different_plans_compared(self, db_path):
        """Intents with different plan_ids are compared."""
        make_intent("c-020", plan_id="plan-A")
        make_intent("c-021", plan_id="plan-B")
        _set_identical_vectors(["c-020", "c-021"])
        candidates = generate_candidates(similarity_threshold=0.5)
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in candidates}
        assert ("c-020", "c-021") in pair_ids

    def test_identical_vectors_high_similarity(self, db_path):
        """Identical vectors produce similarity 1.0."""
        make_intent("c-030", plan_id="plan-X")
        make_intent("c-031", plan_id="plan-Y")
        _set_identical_vectors(["c-030", "c-031"])
        candidates = generate_candidates(similarity_threshold=0.99)
        assert len(candidates) == 1
        assert candidates[0].similarity > 0.99

    def test_different_target_not_compared(self, db_path):
        """Intents targeting different branches are not compared."""
        make_intent("c-040", target="main", plan_id="plan-A")
        make_intent("c-041", target="develop", plan_id="plan-B")
        _set_identical_vectors(["c-040", "c-041"])
        # Target filter: only main
        candidates = generate_candidates(
            target="main", similarity_threshold=0.5,
        )
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in candidates}
        assert ("c-040", "c-041") not in pair_ids

    def test_merged_intent_excluded(self, db_path):
        """Merged intents are not included as candidates."""
        make_intent("c-050", status=Status.MERGED, plan_id="plan-A")
        make_intent("c-051", status=Status.READY, plan_id="plan-B")
        _set_identical_vectors(["c-050", "c-051"])
        candidates = generate_candidates(similarity_threshold=0.0)
        ids = {c.intent_a for c in candidates} | {c.intent_b for c in candidates}
        assert "c-050" not in ids

    def test_no_embedding_skipped(self, db_path):
        """Intents without embeddings are skipped."""
        make_intent("c-060", plan_id="plan-A")
        make_intent("c-061", plan_id="plan-B")
        # Don't index — no embeddings
        candidates = generate_candidates(similarity_threshold=0.0)
        assert candidates == []

    def test_tenant_filter(self, db_path):
        """Tenant filter restricts candidates."""
        make_intent("c-070", tenant_id="team-a", plan_id="plan-A")
        make_intent("c-071", tenant_id="team-a", plan_id="plan-B")
        make_intent("c-072", tenant_id="team-b", plan_id="plan-C")
        _set_identical_vectors(["c-070", "c-071", "c-072"])
        # Only team-a
        candidates = generate_candidates(
            tenant_id="team-a", similarity_threshold=0.0,
        )
        ids = {c.intent_a for c in candidates} | {c.intent_b for c in candidates}
        assert "c-072" not in ids


# ===================================================================
# AR-19: Scoring heuristics
# ===================================================================

class TestScoringHeuristics:
    def test_target_overlap_same(self):
        """Same target → 1.0."""
        a = Intent(id="s-001", source="f/a", target="main", status=Status.READY)
        b = Intent(id="s-002", source="f/b", target="main", status=Status.READY)
        assert _target_overlap(a, b) == 1.0

    def test_target_overlap_different(self):
        """Different target → 0.0."""
        a = Intent(id="s-003", source="f/a", target="main", status=Status.READY)
        b = Intent(id="s-004", source="f/b", target="develop", status=Status.READY)
        assert _target_overlap(a, b) == 0.0

    def test_scope_overlap_full(self):
        """Identical scope hints → 1.0."""
        a = Intent(id="s-005", source="f/a", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth", "api"]})
        b = Intent(id="s-006", source="f/b", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth", "api"]})
        assert _scope_overlap(a, b) == 1.0

    def test_scope_overlap_partial(self):
        """Partial overlap → Jaccard index."""
        a = Intent(id="s-007", source="f/a", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth", "api"]})
        b = Intent(id="s-008", source="f/b", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth", "db"]})
        # intersection={"auth"}, union={"auth","api","db"} → 1/3
        assert abs(_scope_overlap(a, b) - 1.0 / 3.0) < 1e-6

    def test_scope_overlap_none(self):
        """No scope hints → 0.0."""
        a = Intent(id="s-009", source="f/a", target="main", status=Status.READY)
        b = Intent(id="s-010", source="f/b", target="main", status=Status.READY)
        assert _scope_overlap(a, b) == 0.0

    def test_score_combines_signals(self):
        """Score is weighted combination of signals."""
        cand = ConflictCandidate(
            intent_a="s-020", intent_b="s-021",
            similarity=0.9, target="main",
        )
        a = Intent(id="s-020", source="f/a", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth"]})
        b = Intent(id="s-021", source="f/b", target="main", status=Status.READY,
                   technical={"scope_hint": ["auth"]})
        cs = score_conflict(cand, a, b)
        # 0.6*0.9 + 0.2*1.0 + 0.2*1.0 = 0.54 + 0.2 + 0.2 = 0.94
        assert abs(cs.score - 0.94) < 1e-3
        assert cs.similarity == 0.9
        assert cs.target_overlap == 1.0
        assert cs.scope_overlap == 1.0

    def test_score_with_custom_weights(self, db_path):
        """Custom weights are applied."""
        cand = ConflictCandidate(
            intent_a="s-030", intent_b="s-031",
            similarity=0.8, target="main",
        )
        a = Intent(id="s-030", source="f/a", target="main", status=Status.READY)
        b = Intent(id="s-031", source="f/b", target="main", status=Status.READY)
        cs = score_conflict(cand, a, b, w_similarity=1.0, w_target=0.0, w_scope=0.0)
        assert abs(cs.score - 0.8) < 1e-3

    def test_score_includes_plan_info(self, db_path):
        """Score details include plan and origin info."""
        cand = ConflictCandidate(
            intent_a="s-040", intent_b="s-041",
            similarity=0.85, target="main",
        )
        a = Intent(id="s-040", source="f/a", target="main", status=Status.READY,
                   plan_id="plan-X", origin_type="agent")
        b = Intent(id="s-041", source="f/b", target="main", status=Status.READY,
                   plan_id="plan-Y", origin_type="human")
        cs = score_conflict(cand, a, b)
        assert cs.details["plan_a"] == "plan-X"
        assert cs.details["plan_b"] == "plan-Y"
        assert cs.details["origin_a"] == "agent"
        assert cs.details["origin_b"] == "human"


# ===================================================================
# AR-20: Conflict scan and eventing
# ===================================================================

class TestConflictScan:
    def test_scan_empty_db(self, db_path):
        """Scan on empty DB returns no conflicts."""
        report = scan_conflicts()
        assert report.conflicts == []
        assert report.candidates_checked == 0

    def test_scan_detects_similar_intents(self, db_path):
        """Two intents with identical vectors from different plans are detected."""
        make_intent("sc-001", plan_id="plan-A")
        make_intent("sc-002", plan_id="plan-B")
        _set_identical_vectors(["sc-001", "sc-002"])
        report = scan_conflicts(
            conflict_threshold=0.5, similarity_threshold=0.5,
        )
        assert len(report.conflicts) >= 1
        ids = {(c.intent_a, c.intent_b) for c in report.conflicts}
        assert any("sc-001" in pair and "sc-002" in pair for pair in ids)

    def test_scan_emits_events(self, db_path):
        """Scan emits SEMANTIC_CONFLICT_DETECTED events."""
        make_intent("sc-010", plan_id="plan-A")
        make_intent("sc-011", plan_id="plan-B")
        _set_identical_vectors(["sc-010", "sc-011"])
        scan_conflicts(conflict_threshold=0.5, similarity_threshold=0.5)
        events = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
        )
        assert len(events) >= 1
        payload = events[0]["payload"]
        assert "intent_a" in payload
        assert "intent_b" in payload
        assert "score" in payload
        assert payload["mode"] == "shadow"

    def test_scan_respects_threshold(self, db_path):
        """Conflicts below threshold are not reported."""
        make_intent("sc-020", plan_id="plan-A")
        make_intent("sc-021", plan_id="plan-B")
        # Index with real embeddings — different canonical text → low similarity
        provider = get_provider("deterministic")
        _index("sc-020", provider)
        _index("sc-021", provider)
        report = scan_conflicts(
            conflict_threshold=0.99, similarity_threshold=0.0,
        )
        # Different canonical text → low similarity → below 0.99 threshold
        high_score = [c for c in report.conflicts if c.score >= 0.99]
        assert len(high_score) == 0

    def test_scan_mode_shadow(self, db_path):
        """Shadow mode is recorded in report and events."""
        make_intent("sc-030", plan_id="plan-A")
        make_intent("sc-031", plan_id="plan-B")
        _set_identical_vectors(["sc-030", "sc-031"])
        report = scan_conflicts(
            mode="shadow",
            conflict_threshold=0.5, similarity_threshold=0.5,
        )
        assert report.mode == "shadow"

    def test_scan_mode_enforce(self, db_path):
        """Enforce mode is recorded in report."""
        make_intent("sc-040", plan_id="plan-A")
        make_intent("sc-041", plan_id="plan-B")
        _set_identical_vectors(["sc-040", "sc-041"])
        report = scan_conflicts(
            mode="enforce",
            conflict_threshold=0.5, similarity_threshold=0.5,
        )
        assert report.mode == "enforce"


class TestConflictResolution:
    def test_resolve_emits_event(self, db_path):
        """resolve_conflict emits SEMANTIC_CONFLICT_RESOLVED event."""
        result = resolve_conflict(
            "r-001", "r-002",
            resolution="acknowledged",
            resolved_by="alice",
        )
        assert result["ok"] is True
        events = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_RESOLVED,
        )
        assert len(events) >= 1
        p = events[0]["payload"]
        assert p["intent_a"] == "r-001"
        assert p["intent_b"] == "r-002"
        assert p["resolution"] == "acknowledged"

    def test_list_excludes_resolved(self, db_path):
        """list_conflicts excludes resolved pairs."""
        # Emit a detected event
        event_log.append(Event(
            event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
            intent_id="lr-001",
            payload={
                "intent_a": "lr-001", "intent_b": "lr-002",
                "score": 0.85, "mode": "shadow",
            },
        ))
        # Should appear in list
        active = list_conflicts()
        assert any(
            c.get("intent_a") == "lr-001" and c.get("intent_b") == "lr-002"
            for c in active
        )
        # Resolve it
        resolve_conflict("lr-001", "lr-002")
        # Should no longer appear
        active = list_conflicts()
        assert not any(
            c.get("intent_a") == "lr-001" and c.get("intent_b") == "lr-002"
            for c in active
        )

    def test_list_shows_unresolved(self, db_path):
        """list_conflicts shows unresolved conflicts."""
        event_log.append(Event(
            event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
            intent_id="lu-001",
            payload={
                "intent_a": "lu-001", "intent_b": "lu-002",
                "score": 0.9, "mode": "shadow",
            },
        ))
        event_log.append(Event(
            event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
            intent_id="lu-003",
            payload={
                "intent_a": "lu-003", "intent_b": "lu-004",
                "score": 0.75, "mode": "shadow",
            },
        ))
        active = list_conflicts()
        intent_pairs = {(c["intent_a"], c["intent_b"]) for c in active}
        assert ("lu-001", "lu-002") in intent_pairs
        assert ("lu-003", "lu-004") in intent_pairs


# ===================================================================
# AR-21: Integration — scan + resolve + list round-trip
# ===================================================================

class TestConflictRoundTrip:
    def test_full_lifecycle(self, db_path):
        """Full cycle: create intents → set vectors → scan → resolve → list."""
        make_intent("rt-001", plan_id="plan-A")
        make_intent("rt-002", plan_id="plan-B")
        _set_identical_vectors(["rt-001", "rt-002"])

        # Scan
        report = scan_conflicts(
            conflict_threshold=0.5, similarity_threshold=0.5,
        )
        assert len(report.conflicts) >= 1

        # List shows unresolved
        active = list_conflicts()
        assert len(active) >= 1

        # Resolve
        resolve_conflict("rt-001", "rt-002",
                        resolution="merged-plans", resolved_by="admin")

        # List no longer shows it
        active = list_conflicts()
        matching = [
            c for c in active
            if {c["intent_a"], c["intent_b"]} == {"rt-001", "rt-002"}
        ]
        assert len(matching) == 0

    def test_null_plan_intents_compared(self, db_path):
        """Intents without plan_id are compared against each other."""
        make_intent("np-001", plan_id=None)
        make_intent("np-002", plan_id=None)
        _set_identical_vectors(["np-001", "np-002"])
        candidates = generate_candidates(
            similarity_threshold=0.5,
        )
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in candidates}
        assert ("np-001", "np-002") in pair_ids

    def test_mixed_plan_and_null(self, db_path):
        """Intent with plan_id vs intent without plan_id are compared."""
        make_intent("mx-001", plan_id="plan-A")
        make_intent("mx-002", plan_id=None)
        _set_identical_vectors(["mx-001", "mx-002"])
        candidates = generate_candidates(
            similarity_threshold=0.5,
        )
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in candidates}
        assert ("mx-001", "mx-002") in pair_ids

    def test_queued_intents_included(self, db_path):
        """QUEUED intents are included in conflict scan."""
        make_intent("q-001", status=Status.QUEUED, plan_id="plan-A")
        make_intent("q-002", status=Status.READY, plan_id="plan-B")
        _set_identical_vectors(["q-001", "q-002"])
        candidates = generate_candidates(
            similarity_threshold=0.5,
        )
        ids = {c.intent_a for c in candidates} | {c.intent_b for c in candidates}
        assert "q-001" in ids

    def test_event_evidence_includes_thresholds(self, db_path):
        """Conflict events include threshold info in evidence."""
        make_intent("ev-001", plan_id="plan-A")
        make_intent("ev-002", plan_id="plan-B")
        _set_identical_vectors(["ev-001", "ev-002"])
        scan_conflicts(
            conflict_threshold=0.4,
            similarity_threshold=0.5,
        )
        events = event_log.query(
        event_type=EventType.SEMANTIC_CONFLICT_DETECTED,
        )
        assert len(events) >= 1
        evidence = events[0].get("evidence", {})
        assert evidence["conflict_threshold"] == 0.4


# ===================================================================
# E2E: semantic text fix — indexer produces comparable embeddings
# ===================================================================

class TestSemanticTextFix:
    def test_same_semantic_different_id_produces_same_embedding(self, db_path):
        """Two intents with different IDs but same semantic content produce identical embeddings."""
        common = dict(
            source="feature/login", target="main",
            risk_level=RiskLevel.HIGH, priority=1,
            semantic={"objective": "Add auth", "problem_statement": "Need login"},
            technical={"scope_hint": ["auth"]},
        )
        make_intent("fix-001", plan_id="plan-A", **common)
        make_intent("fix-002", plan_id="plan-B", **common)

        provider = get_provider("deterministic")
        _index("fix-001", provider)
        _index("fix-002", provider)

        emb1 = event_log.get_embedding("fix-001", "deterministic-v1")
        emb2 = event_log.get_embedding("fix-002", "deterministic-v1")
        assert emb1 is not None
        assert emb2 is not None

        v1 = json.loads(emb1["vector"])
        v2 = json.loads(emb2["vector"])
        sim = _cosine_similarity(v1, v2)
        assert abs(sim - 1.0) < 1e-6, f"Expected cosine similarity ~1.0, got {sim}"

    def test_scan_detects_duplicate_intents_via_indexer(self, db_path):
        """E2E: create intents with same semantic content, index them, scan detects conflict."""
        common = dict(
            source="feature/auth", target="main",
            risk_level=RiskLevel.MEDIUM, priority=1,
            semantic={"objective": "Implement SSO", "problem_statement": "Need single sign-on"},
            technical={"scope_hint": ["auth", "sso"]},
        )
        make_intent("e2e-001", plan_id="plan-X", **common)
        make_intent("e2e-002", plan_id="plan-Y", **common)

        provider = get_provider("deterministic")
        _index("e2e-001", provider)
        _index("e2e-002", provider)

        report = scan_conflicts(
            conflict_threshold=0.5, similarity_threshold=0.5,
        )
        pair_ids = {tuple(sorted((c.intent_a, c.intent_b))) for c in report.conflicts}
        assert ("e2e-001", "e2e-002") in pair_ids, (
            f"Expected conflict between e2e-001 and e2e-002, got: {pair_ids}"
        )


class TestThresholdCalibration:
    """Auto-threshold selection based on model type."""

    def test_deterministic_model_uses_high_threshold(self, db_path):
        """Deterministic model auto-selects 0.95 similarity threshold."""
        from converge.semantic.conflicts import _effective_similarity_threshold
        assert _effective_similarity_threshold("deterministic-v1", None) == 0.95

    def test_semantic_model_uses_default_threshold(self, db_path):
        """Non-deterministic model uses 0.70 default."""
        from converge.semantic.conflicts import _effective_similarity_threshold
        assert _effective_similarity_threshold("sentence-transformers", None) == 0.70

    def test_explicit_threshold_overrides(self, db_path):
        """Explicit threshold always wins over auto-selection."""
        from converge.semantic.conflicts import _effective_similarity_threshold
        assert _effective_similarity_threshold("deterministic-v1", 0.50) == 0.50
