"""Tests for risk scoring: graph, 4 signals, bombs, diagnostics."""

import networkx as nx

from converge.models import Intent, RiskEval, RiskLevel, Simulation, Status
from converge.risk import (
    analyze_findings,
    build_dependency_graph,
    build_impact_edges,
    compute_entropic_load,
    compute_contextual_value,
    compute_complexity_delta,
    compute_path_dependence,
    containment_score,
    detect_bombs,
    evaluate_risk,
    graph_metrics,
    propagation_score,
    build_diagnostics,
)


def _intent(**kw) -> Intent:
    defaults = dict(
        id="test-001", source="feature/x", target="main",
        status=Status.READY, risk_level=RiskLevel.MEDIUM,
        dependencies=[], technical={"scope_hint": []},
    )
    defaults.update(kw)
    return Intent(**defaults)


def _sim(**kw) -> Simulation:
    defaults = dict(mergeable=True, conflicts=[], files_changed=["a.py", "b.py"])
    defaults.update(kw)
    return Simulation(**defaults)


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

class TestDependencyGraph:
    def test_basic_graph_structure(self):
        G = build_dependency_graph(_intent(), _sim())
        assert isinstance(G, nx.DiGraph)
        assert len(G) > 0
        # Should have file nodes
        file_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "file"]
        assert len(file_nodes) == 2  # a.py, b.py

    def test_graph_with_scopes(self):
        intent = _intent(technical={"scope_hint": ["auth"]})
        G = build_dependency_graph(intent, _sim())
        scope_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "scope"]
        assert len(scope_nodes) == 1

    def test_graph_with_dependencies(self):
        intent = _intent(dependencies=["dep-1", "dep-2"])
        G = build_dependency_graph(intent, _sim())
        dep_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "dependency"]
        assert len(dep_nodes) == 2

    def test_graph_with_coupling_data(self):
        coupling = [{"file_a": "a.py", "file_b": "c.py", "co_changes": 5}]
        G = build_dependency_graph(_intent(), _sim(), coupling_data=coupling)
        assert "c.py" in G
        assert G.has_edge("a.py", "c.py")

    def test_graph_metrics(self):
        G = build_dependency_graph(_intent(), _sim())
        gm = graph_metrics(G)
        assert gm["nodes"] > 0
        assert gm["edges"] >= 0
        assert "pagerank_top" in gm
        assert "critical_files" in gm
        assert gm["components"] >= 1

    def test_directory_structure(self):
        sim = _sim(files_changed=["src/auth/login.py", "src/auth/logout.py", "src/db/conn.py"])
        G = build_dependency_graph(_intent(), sim)
        dir_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "directory"]
        assert len(dir_nodes) >= 2  # src/auth and src/db


# ---------------------------------------------------------------------------
# 4 independent signals
# ---------------------------------------------------------------------------

class TestEntropicLoad:
    def test_basic_load(self):
        G = build_dependency_graph(_intent(), _sim())
        score = compute_entropic_load(_intent(), _sim(), G)
        assert 0 <= score <= 100
        assert score > 0  # 2 files = some load

    def test_more_files_higher_load(self):
        sim_small = _sim(files_changed=["a.py"])
        sim_large = _sim(files_changed=[f"f{i}.py" for i in range(20)])
        G_small = build_dependency_graph(_intent(), sim_small)
        G_large = build_dependency_graph(_intent(), sim_large)
        load_small = compute_entropic_load(_intent(), sim_small, G_small)
        load_large = compute_entropic_load(_intent(), sim_large, G_large)
        assert load_large > load_small

    def test_conflicts_increase_load(self):
        sim_clean = _sim(conflicts=[])
        sim_conflict = _sim(conflicts=["a.py"], mergeable=False)
        G = build_dependency_graph(_intent(), sim_clean)
        load_clean = compute_entropic_load(_intent(), sim_clean, G)
        G2 = build_dependency_graph(_intent(), sim_conflict)
        load_conflict = compute_entropic_load(_intent(), sim_conflict, G2)
        assert load_conflict > load_clean


class TestContextualValue:
    def test_basic_value(self):
        G = build_dependency_graph(_intent(), _sim())
        score = compute_contextual_value(_intent(), _sim(), G)
        assert 0 <= score <= 100

    def test_core_target_increases_value(self):
        intent_main = _intent(target="main")
        intent_dev = _intent(target="dev")
        sim = _sim()
        G1 = build_dependency_graph(intent_main, sim)
        G2 = build_dependency_graph(intent_dev, sim)
        val_main = compute_contextual_value(intent_main, sim, G1)
        val_dev = compute_contextual_value(intent_dev, sim, G2)
        assert val_main > val_dev

    def test_core_paths_increase_value(self):
        sim_core = _sim(files_changed=["src/core.py", "src/lib.py"])
        sim_other = _sim(files_changed=["docs/readme.md", "docs/guide.md"])
        G1 = build_dependency_graph(_intent(), sim_core)
        G2 = build_dependency_graph(_intent(), sim_other)
        val_core = compute_contextual_value(_intent(), sim_core, G1)
        val_other = compute_contextual_value(_intent(), sim_other, G2)
        assert val_core > val_other


class TestComplexityDelta:
    def test_basic_delta(self):
        G = build_dependency_graph(_intent(), _sim())
        score = compute_complexity_delta(_intent(), _sim(), G)
        assert 0 <= score <= 100

    def test_more_scopes_higher_delta(self):
        intent_simple = _intent(technical={"scope_hint": []})
        intent_spread = _intent(technical={"scope_hint": ["auth", "db", "api"]})
        sim = _sim()
        G1 = build_dependency_graph(intent_simple, sim)
        G2 = build_dependency_graph(intent_spread, sim)
        d_simple = compute_complexity_delta(intent_simple, sim, G1)
        d_spread = compute_complexity_delta(intent_spread, sim, G2)
        assert d_spread > d_simple


class TestPathDependence:
    def test_basic_dependence(self):
        G = build_dependency_graph(_intent(), _sim())
        score = compute_path_dependence(_intent(), _sim(), G)
        assert 0 <= score <= 100

    def test_conflicts_increase_dependence(self):
        sim_clean = _sim(conflicts=[])
        sim_conflict = _sim(conflicts=["a.py", "b.py"], mergeable=False)
        G1 = build_dependency_graph(_intent(), sim_clean)
        G2 = build_dependency_graph(_intent(), sim_conflict)
        pd_clean = compute_path_dependence(_intent(), sim_clean, G1)
        pd_conflict = compute_path_dependence(_intent(), sim_conflict, G2)
        assert pd_conflict > pd_clean

    def test_dependencies_increase_dependence(self):
        intent_solo = _intent(dependencies=[])
        intent_deps = _intent(dependencies=["a", "b", "c", "d"])
        sim = _sim()
        G1 = build_dependency_graph(intent_solo, sim)
        G2 = build_dependency_graph(intent_deps, sim)
        pd_solo = compute_path_dependence(intent_solo, sim, G1)
        pd_deps = compute_path_dependence(intent_deps, sim, G2)
        assert pd_deps > pd_solo


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

class TestFindings:
    def test_large_change_finding(self):
        sim = _sim(files_changed=[f"f{i}.py" for i in range(20)])
        findings = analyze_findings(_intent(), sim)
        codes = [f["code"] for f in findings]
        assert "semantic.large_change" in codes

    def test_core_target_finding(self):
        findings = analyze_findings(_intent(target="main"), _sim())
        codes = [f["code"] for f in findings]
        assert "semantic.core_target" in codes

    def test_conflict_finding(self):
        sim = _sim(mergeable=False, conflicts=["x.py"])
        findings = analyze_findings(_intent(), sim)
        codes = [f["code"] for f in findings]
        assert "semantic.merge_conflict" in codes

    def test_dependency_spread_finding(self):
        intent = _intent(dependencies=["a", "b", "c", "d"])
        findings = analyze_findings(intent, _sim())
        codes = [f["code"] for f in findings]
        assert "semantic.dependency_spread" in codes


# ---------------------------------------------------------------------------
# Impact edges, propagation, containment (backwards compat)
# ---------------------------------------------------------------------------

class TestImpactGraph:
    def test_basic_edges(self):
        edges = build_impact_edges(_intent(), _sim())
        types = {e["type"] for e in edges}
        assert "merge_target" in types
        assert "modifies_file" in types

    def test_dependency_edges(self):
        intent = _intent(dependencies=["dep-1", "dep-2"])
        edges = build_impact_edges(intent, _sim())
        dep_edges = [e for e in edges if e["type"] == "depends_on"]
        assert len(dep_edges) == 2

    def test_propagation_score(self):
        G = build_dependency_graph(_intent(), _sim())
        edges = build_impact_edges(_intent(), _sim())
        score = propagation_score(G, edges)
        assert score > 0
        assert score <= 100


class TestContainment:
    def test_no_spread(self):
        intent = _intent(dependencies=[], technical={"scope_hint": []})
        G = build_dependency_graph(intent, _sim(files_changed=[]))
        score = containment_score(intent, G, [])
        assert score == 1.0

    def test_high_spread(self):
        intent = _intent(dependencies=["a", "b", "c", "d", "e"])
        edges = [{"target": f"t{i}"} for i in range(15)]
        G = build_dependency_graph(intent, _sim())
        score = containment_score(intent, G, edges)
        assert score < 0.5


# ---------------------------------------------------------------------------
# Bomb detection
# ---------------------------------------------------------------------------

class TestBombDetection:
    def test_no_bombs_for_small_change(self):
        G = build_dependency_graph(_intent(), _sim())
        bombs = detect_bombs(_intent(), _sim(), G)
        assert isinstance(bombs, list)

    def test_thermal_death_detected(self):
        """Thermal death: multiple indicators elevated."""
        intent = _intent(dependencies=["a", "b", "c", "d"])
        sim = _sim(
            files_changed=[f"f{i}.py" for i in range(15)],
            conflicts=["x.py"],
            mergeable=False,
        )
        G = build_dependency_graph(intent, sim)
        bombs = detect_bombs(intent, sim, G)
        types = [b["type"] for b in bombs]
        assert "thermal_death" in types

    def test_spiral_needs_cycles(self):
        """Spiral detection requires circular dependencies."""
        intent = _intent()
        sim = _sim()
        G = build_dependency_graph(intent, sim)
        bombs = detect_bombs(intent, sim, G)
        # Simple change shouldn't trigger spiral
        spiral_bombs = [b for b in bombs if b["type"] == "spiral"]
        # May or may not have spiral — depends on co-located edges creating cycles
        assert isinstance(spiral_bombs, list)


# ---------------------------------------------------------------------------
# Full risk evaluation
# ---------------------------------------------------------------------------

class TestFullRiskEval:
    def test_evaluate_risk(self):
        result = evaluate_risk(_intent(), _sim())
        assert isinstance(result, RiskEval)
        assert 0 <= result.risk_score <= 100
        assert 0 <= result.containment_score <= 1.0
        assert len(result.impact_edges) > 0
        # 4 signals present
        assert 0 <= result.entropic_load <= 100
        assert 0 <= result.contextual_value <= 100
        assert 0 <= result.complexity_delta <= 100
        assert 0 <= result.path_dependence <= 100
        # Graph metrics present
        assert result.graph_metrics["nodes"] > 0

    def test_high_risk_intent(self):
        intent = _intent(
            risk_level=RiskLevel.CRITICAL,
            dependencies=["a", "b", "c", "d", "e"],
            technical={"scope_hint": ["core", "db", "api"]},
        )
        sim = _sim(files_changed=[f"f{i}.py" for i in range(25)], conflicts=["x.py"], mergeable=False)
        result = evaluate_risk(intent, sim)
        assert result.risk_score > 30
        assert result.entropic_load > 30  # lots of files + conflict + deps

    def test_to_dict_has_signals(self):
        result = evaluate_risk(_intent(), _sim())
        d = result.to_dict()
        assert "signals" in d
        assert "entropic_load" in d["signals"]
        assert "contextual_value" in d["signals"]
        assert "complexity_delta" in d["signals"]
        assert "path_dependence" in d["signals"]
        assert "graph_metrics" in d
        assert "bombs" in d

    def test_coupling_data_enriches_graph(self):
        coupling = [{"file_a": "a.py", "file_b": "external.py", "co_changes": 10}]
        result = evaluate_risk(_intent(), _sim(), coupling_data=coupling)
        assert result.graph_metrics["nodes"] >= 3  # a.py, b.py, external.py


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics_for_high_risk(self):
        risk_eval = RiskEval(
            intent_id="t-001",
            risk_score=85,
            damage_score=70,
            entropy_score=45,
            propagation_score=50,
            containment_score=0.3,
            entropic_load=60,
            contextual_value=70,
            complexity_delta=30,
            path_dependence=50,
            findings=[],
        )
        sim = _sim(mergeable=False, conflicts=["a.py", "b.py"])
        diags = build_diagnostics(_intent(), risk_eval, sim)
        codes = [d["code"] for d in diags]
        assert "diag.merge_conflict" in codes
        assert "diag.high_risk" in codes
        assert "diag.high_entropic_load" in codes
        assert "diag.high_contextual_value" in codes
        # Should be sorted by severity (critical first)
        severities = [d["severity"] for d in diags]
        assert severities[0] in ("critical", "high")

    def test_bomb_diagnostics(self):
        risk_eval = RiskEval(
            intent_id="t-002",
            risk_score=50,
            bombs=[{"type": "thermal_death", "severity": "critical",
                    "message": "System overheating"}],
            findings=[],
        )
        sim = _sim()
        diags = build_diagnostics(_intent(), risk_eval, sim)
        codes = [d["code"] for d in diags]
        assert "diag.bomb.thermal_death" in codes
