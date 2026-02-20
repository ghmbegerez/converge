"""Risk scoring: dependency graph, 4 independent signals, bomb detection, diagnostics.

Uses NetworkX for real dependency graph analysis with PageRank.
Produces 4 orthogonal signals instead of a single risk score:
  - entropic_load:    disorder the change introduces
  - contextual_value: importance of the files being changed (PageRank)
  - complexity_delta: net complexity change to the system
  - path_dependence:  sensitivity to merge order

Bomb detection identifies structural degradation patterns:
  - cascade:       change generating chain reactions via high-centrality nodes
  - spiral:        circular dependency coupling increasing
  - thermal_death: multiple entropy indicators elevated simultaneously
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import networkx as nx

from converge.models import Intent, RiskEval, Simulation


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RISK_BONUS = {"low": 0, "medium": 5, "high": 15, "critical": 30}
_CORE_TARGETS = {"main", "master", "release", "production", "prod"}
_CORE_PATHS = {"src/", "lib/", "core/", "pkg/", "internal/", "app/"}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ---------------------------------------------------------------------------
# Dependency graph (NetworkX)
# ---------------------------------------------------------------------------

def build_dependency_graph(
    intent: Intent,
    simulation: Simulation,
    coupling_data: list[dict[str, Any]] | None = None,
) -> nx.DiGraph:
    """Build a directed dependency graph from intent + simulation data.

    Nodes: files changed, directories, scopes, dependencies.
    Edges: file→dir (containment), file→file (coupling), scope→file, dep→intent.
    """
    G = nx.DiGraph()

    # Add file nodes with attributes
    for f in simulation.files_changed:
        G.add_node(f, kind="file", path=f)

    # Directory containment edges (file → parent dir)
    dirs_seen: set[str] = set()
    for f in simulation.files_changed:
        parts = PurePosixPath(f).parts
        if len(parts) > 1:
            parent = str(PurePosixPath(*parts[:-1]))
            if parent not in dirs_seen:
                G.add_node(parent, kind="directory")
                dirs_seen.add(parent)
            G.add_edge(f, parent, rel="contained_in", weight=0.3)

    # Connect files in the same directory (proximity coupling)
    dir_files: dict[str, list[str]] = {}
    for f in simulation.files_changed:
        parts = PurePosixPath(f).parts
        parent = str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else "."
        dir_files.setdefault(parent, []).append(f)

    for files in dir_files.values():
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                if not G.has_edge(f1, f2):
                    G.add_edge(f1, f2, rel="co_located", weight=0.2)
                if not G.has_edge(f2, f1):
                    G.add_edge(f2, f1, rel="co_located", weight=0.2)

    # Scope hint edges
    for scope in intent.technical.get("scope_hint", []):
        G.add_node(scope, kind="scope")
        for f in simulation.files_changed:
            if scope.lower() in f.lower():
                G.add_edge(scope, f, rel="scope_contains", weight=0.5)
            else:
                G.add_edge(scope, f, rel="scope_touches", weight=0.2)

    # Dependency edges
    for dep in intent.dependencies:
        G.add_node(dep, kind="dependency")
        G.add_edge(intent.id, dep, rel="depends_on", weight=0.8)

    # Intent node
    G.add_node(intent.id, kind="intent")
    G.add_edge(intent.id, intent.target, rel="merge_target", weight=1.0)
    if intent.target not in G:
        G.add_node(intent.target, kind="branch")

    # External coupling data (from archaeology)
    if coupling_data:
        changed_set = set(simulation.files_changed)
        for c in coupling_data:
            a, b = c.get("file_a", ""), c.get("file_b", "")
            co_changes = c.get("co_changes", 1)
            # Only add if at least one file is in this change
            if a in changed_set or b in changed_set:
                w = min(1.0, co_changes * 0.1)
                if a not in G:
                    G.add_node(a, kind="file", path=a)
                if b not in G:
                    G.add_node(b, kind="file", path=b)
                G.add_edge(a, b, rel="co_change", weight=w)
                G.add_edge(b, a, rel="co_change", weight=w)

    return G


def graph_metrics(G: nx.DiGraph) -> dict[str, Any]:
    """Extract key metrics from the dependency graph."""
    if len(G) == 0:
        return {"nodes": 0, "edges": 0, "pagerank_max": 0.0, "pagerank_top": [],
                "components": 0, "density": 0.0}

    pr = nx.pagerank(G, weight="weight")
    top_pr = sorted(pr.items(), key=lambda x: -x[1])[:10]

    # Weakly connected components on undirected view
    n_components = nx.number_weakly_connected_components(G)

    # Density
    density = nx.density(G)

    # Find critical files (high PageRank, kind=file)
    critical_files = []
    for node, rank in top_pr:
        data = G.nodes.get(node, {})
        if data.get("kind") == "file":
            critical_files.append({"file": node, "pagerank": round(rank, 4)})

    return {
        "nodes": len(G),
        "edges": G.number_of_edges(),
        "pagerank_max": round(top_pr[0][1], 4) if top_pr else 0.0,
        "pagerank_top": [{"node": n, "rank": round(r, 4)} for n, r in top_pr[:5]],
        "critical_files": critical_files[:5],
        "components": n_components,
        "density": round(density, 4),
    }


# ---------------------------------------------------------------------------
# 4 independent signals
# ---------------------------------------------------------------------------

def compute_entropic_load(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
) -> float:
    """Entropic Load: how much disorder this change introduces.

    Based on: file count, conflict count, unique directories, dependency count,
    and graph dispersion (number of weakly connected components).
    Score 0-100.
    """
    files_count = len(simulation.files_changed)
    conflict_count = len(simulation.conflicts)
    deps_count = len(intent.dependencies)

    # Count unique directories
    dirs = set()
    for f in simulation.files_changed:
        parts = PurePosixPath(f).parts
        if len(parts) > 1:
            dirs.add(str(PurePosixPath(*parts[:-1])))
    dir_spread = len(dirs)

    # Graph dispersion
    n_components = nx.number_weakly_connected_components(G) if len(G) > 0 else 1

    # Weighted sum, normalized to 0-100
    raw = (
        files_count * 2.0 +
        conflict_count * 15.0 +
        deps_count * 6.0 +
        dir_spread * 3.0 +
        (n_components - 1) * 5.0
    )
    return min(100.0, round(raw, 1))


def compute_contextual_value(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
) -> float:
    """Contextual Value: how important are the files being changed.

    Based on: PageRank of touched files (centrality in the graph),
    core path detection, target branch criticality.
    Score 0-100.
    """
    if len(G) == 0:
        return 0.0

    pr = nx.pagerank(G, weight="weight")

    # Sum PageRank of changed files
    file_pr_sum = sum(pr.get(f, 0.0) for f in simulation.files_changed)
    # Normalize: in a uniform graph each node gets 1/N
    n = max(len(G), 1)
    expected_per_file = 1.0 / n
    # How much more important than average?
    importance_ratio = file_pr_sum / (expected_per_file * max(len(simulation.files_changed), 1))

    # Core path bonus
    core_touches = sum(1 for f in simulation.files_changed
                       if any(f.startswith(cp) for cp in _CORE_PATHS))
    core_ratio = core_touches / max(len(simulation.files_changed), 1)

    # Target branch bonus
    target_bonus = 10.0 if intent.target in _CORE_TARGETS else 0.0

    raw = (
        min(importance_ratio * 30.0, 60.0) +
        core_ratio * 20.0 +
        target_bonus +
        _RISK_BONUS.get(intent.risk_level.value, 5)
    )
    return min(100.0, round(raw, 1))


def compute_complexity_delta(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
) -> float:
    """Complexity Delta: net change in system complexity.

    Based on: graph density, scope spread, edge-to-node ratio,
    number of cross-directory edges.
    Score 0-100.
    """
    if len(G) == 0:
        return 0.0

    density = nx.density(G)
    edge_node_ratio = G.number_of_edges() / max(len(G), 1)

    # Cross-directory edges (signals architectural spread)
    cross_dir = 0
    for u, v in G.edges():
        u_data = G.nodes.get(u, {})
        v_data = G.nodes.get(v, {})
        if u_data.get("kind") == "file" and v_data.get("kind") == "file":
            u_dir = str(PurePosixPath(u).parent)
            v_dir = str(PurePosixPath(v).parent)
            if u_dir != v_dir:
                cross_dir += 1

    scope_count = len(intent.technical.get("scope_hint", []))

    raw = (
        density * 40.0 +
        min(edge_node_ratio * 10.0, 30.0) +
        cross_dir * 3.0 +
        scope_count * 5.0
    )
    return min(100.0, round(raw, 1))


def compute_path_dependence(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
) -> float:
    """Path Dependence: how sensitive is this change to merge order.

    Based on: conflicts (direct ordering failure), files in core paths
    (likely to be touched by others), dependency chain length,
    graph cycles.
    Score 0-100.
    """
    conflict_count = len(simulation.conflicts)
    deps_count = len(intent.dependencies)

    # Files likely to collide with others (core paths)
    core_touches = sum(1 for f in simulation.files_changed
                       if any(f.startswith(cp) for cp in _CORE_PATHS))

    # Cycles in the graph (circular dependencies) — cap enumeration
    cycle_count = 0
    try:
        if not nx.is_directed_acyclic_graph(G):
            for cycle in nx.simple_cycles(G):
                if len(cycle) >= 2:
                    cycle_count += 1
                if cycle_count >= 20:
                    break
    except Exception:
        pass

    # Longest path in DAG (if acyclic)
    try:
        if nx.is_directed_acyclic_graph(G):
            longest = nx.dag_longest_path_length(G)
        else:
            longest = 0
    except Exception:
        longest = 0

    raw = (
        conflict_count * 20.0 +
        core_touches * 4.0 +
        deps_count * 8.0 +
        cycle_count * 5.0 +
        longest * 2.0
    )
    return min(100.0, round(raw, 1))


# ---------------------------------------------------------------------------
# Findings (semantic analysis)
# ---------------------------------------------------------------------------

def analyze_findings(intent: Intent, simulation: Simulation) -> list[dict[str, Any]]:
    """Generate specific findings from intent + simulation."""
    findings = []
    files_count = len(simulation.files_changed)
    deps_count = len(intent.dependencies)
    conflict_count = len(simulation.conflicts)

    if files_count > 15:
        findings.append({"code": "semantic.large_change", "severity": "high",
                         "message": f"Change touches {files_count} files"})
    if deps_count > 3:
        findings.append({"code": "semantic.dependency_spread", "severity": "medium",
                         "message": f"Depends on {deps_count} other intents"})
    if intent.target in _CORE_TARGETS:
        findings.append({"code": "semantic.core_target", "severity": "high",
                         "message": f"Targets core branch: {intent.target}"})
    if conflict_count > 0:
        findings.append({"code": "semantic.merge_conflict", "severity": "critical",
                         "message": f"{conflict_count} merge conflict(s) detected"})

    return findings


# ---------------------------------------------------------------------------
# Legacy compatibility: impact edges, propagation, containment
# ---------------------------------------------------------------------------

def build_impact_edges(intent: Intent, simulation: Simulation) -> list[dict[str, Any]]:
    """Build directed impact edges (flat list for backwards compat)."""
    edges = []
    edges.append({"source": intent.source, "target": intent.target,
                  "type": "merge_target", "weight": 1.0})
    for dep in intent.dependencies:
        edges.append({"source": intent.id, "target": dep,
                      "type": "depends_on", "weight": 0.8})
    for scope in intent.technical.get("scope_hint", []):
        edges.append({"source": intent.id, "target": scope,
                      "type": "touches_scope", "weight": 0.5})
    for f in simulation.files_changed[:20]:
        edges.append({"source": intent.id, "target": f,
                      "type": "modifies_file", "weight": 0.3})
    return edges


def propagation_score(G: nx.DiGraph, edges: list[dict[str, Any]]) -> float:
    """Score based on graph reachability and edge weights."""
    if len(G) == 0 and not edges:
        return 0.0

    # Graph-based: average out-degree of file nodes
    file_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "file"]
    if file_nodes:
        avg_out = sum(G.out_degree(n) for n in file_nodes) / len(file_nodes)
        graph_component = min(50.0, avg_out * 10.0)
    else:
        graph_component = 0.0

    # Edge-based (legacy): weight sum + unique targets
    total_weight = sum(e.get("weight", 0.5) for e in edges)
    unique_targets = len({e["target"] for e in edges})
    edge_component = min(50.0, total_weight * 3.0 + unique_targets * 2.0)

    return min(100.0, round(graph_component + edge_component, 1))


def containment_score(intent: Intent, G: nx.DiGraph, edges: list[dict[str, Any]]) -> float:
    """How contained is the change? 1.0 = perfectly contained, 0.0 = max spread.

    Uses graph components and boundary crossings.
    """
    if len(G) == 0 and not edges:
        return 1.0

    # Count boundary tokens
    boundary_tokens = set()
    for e in edges:
        boundary_tokens.add(e["target"])
    for dep in intent.dependencies:
        boundary_tokens.add(dep)
    for s in intent.technical.get("scope_hint", []):
        boundary_tokens.add(s)

    crossings = len(boundary_tokens)
    if crossings == 0:
        return 1.0

    # Graph enrichment: penalize more components (fragmented change)
    n_components = nx.number_weakly_connected_components(G) if len(G) > 0 else 1
    component_penalty = (n_components - 1) * 0.03

    return round(max(0.0, 1.0 - (crossings * 0.05) - component_penalty), 2)


# ---------------------------------------------------------------------------
# Bomb detection
# ---------------------------------------------------------------------------

def detect_bombs(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Detect structural degradation patterns (bombs).

    - cascade: change touching high-PageRank files that fan out widely
    - spiral: circular dependencies detected in the graph
    - thermal_death: entropy indicators all elevated simultaneously
    """
    bombs = []

    if len(G) == 0:
        return bombs

    pr = nx.pagerank(G, weight="weight")

    # --- Cascade detection ---
    # A change touches high-centrality nodes with high fan-out
    file_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "file"]
    high_pr_files = [f for f in file_nodes
                     if pr.get(f, 0) > 1.5 / max(len(G), 1)]
    high_fanout = [f for f in high_pr_files if G.out_degree(f) >= 3]

    if high_fanout:
        affected = set()
        for f in high_fanout:
            affected.update(nx.descendants(G, f))
        if len(affected) > len(simulation.files_changed) * 1.5:
            bombs.append({
                "type": "cascade",
                "severity": "high",
                "message": f"Change touches {len(high_fanout)} high-centrality node(s) "
                           f"with potential cascade to {len(affected)} nodes",
                "trigger_nodes": high_fanout[:5],
                "blast_radius": len(affected),
            })

    # --- Spiral detection ---
    # Circular dependencies in the graph (cap enumeration to avoid slowness)
    try:
        significant_cycles = []
        if not nx.is_directed_acyclic_graph(G):
            for cycle in nx.simple_cycles(G):
                if len(cycle) >= 2:
                    significant_cycles.append(cycle)
                if len(significant_cycles) >= 10:
                    break
        if len(significant_cycles) >= 2:
            bombs.append({
                "type": "spiral",
                "severity": "medium",
                "message": f"{len(significant_cycles)} circular dependency cycle(s) detected",
                "cycles": [c[:5] for c in significant_cycles[:3]],
            })
    except Exception:
        pass

    # --- Thermal death detection ---
    # Multiple entropy indicators elevated simultaneously
    files_count = len(simulation.files_changed)
    conflict_count = len(simulation.conflicts)
    deps_count = len(intent.dependencies)
    n_components = nx.number_weakly_connected_components(G)

    hot_indicators = 0
    if files_count > 10:
        hot_indicators += 1
    if conflict_count > 0:
        hot_indicators += 1
    if deps_count > 3:
        hot_indicators += 1
    if n_components > 3:
        hot_indicators += 1
    if len(G.edges()) > len(G.nodes()) * 2:
        hot_indicators += 1

    if hot_indicators >= 3:
        bombs.append({
            "type": "thermal_death",
            "severity": "critical",
            "message": f"{hot_indicators}/5 entropy indicators elevated: "
                       f"files={files_count}, conflicts={conflict_count}, "
                       f"deps={deps_count}, components={n_components}, "
                       f"edge_density={G.number_of_edges()}/{len(G)}",
            "indicators": hot_indicators,
        })

    return bombs


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def build_diagnostics(
    intent: Intent,
    risk_eval: RiskEval,
    simulation: Simulation,
) -> list[dict[str, Any]]:
    """Generate explanatory diagnostics from risk evaluation."""
    diags = []

    if risk_eval.risk_score > 60:
        diags.append({
            "severity": "critical" if risk_eval.risk_score > 80 else "high",
            "code": "diag.high_risk",
            "explanation": f"Combined risk score {risk_eval.risk_score:.0f} exceeds safe threshold",
            "recommendation": "Split this change into smaller, independent intents",
        })

    if risk_eval.entropy_score > 20:
        diags.append({
            "severity": "high" if risk_eval.entropy_score > 40 else "medium",
            "code": "diag.high_entropy",
            "explanation": f"Entropy score {risk_eval.entropy_score:.0f} indicates high disorder",
            "recommendation": "Reduce file count or dependencies before merging",
        })

    if not simulation.mergeable:
        diags.append({
            "severity": "critical",
            "code": "diag.merge_conflict",
            "explanation": f"Merge has {len(simulation.conflicts)} conflict(s): {', '.join(simulation.conflicts[:5])}",
            "recommendation": "Resolve conflicts in source branch before retrying",
        })

    if risk_eval.propagation_score > 40:
        diags.append({
            "severity": "high",
            "code": "diag.high_propagation",
            "explanation": f"Change propagation score {risk_eval.propagation_score:.0f} indicates wide blast radius",
            "recommendation": "Review impact graph and consider narrowing scope",
        })

    if risk_eval.containment_score < 0.4:
        diags.append({
            "severity": "medium",
            "code": "diag.low_containment",
            "explanation": f"Containment {risk_eval.containment_score:.2f} is below acceptable levels",
            "recommendation": "Add scope hints or reduce cross-boundary dependencies",
        })

    # Signal-specific diagnostics
    if risk_eval.entropic_load > 50:
        diags.append({
            "severity": "high",
            "code": "diag.high_entropic_load",
            "explanation": f"Entropic load {risk_eval.entropic_load:.0f} indicates high disorder introduction",
            "recommendation": "Reduce the number of files, directories, or dependencies touched",
        })

    if risk_eval.contextual_value > 60:
        diags.append({
            "severity": "high",
            "code": "diag.high_contextual_value",
            "explanation": f"Change touches critical files (contextual value: {risk_eval.contextual_value:.0f})",
            "recommendation": "Ensure thorough review — these files have high centrality in the codebase",
        })

    if risk_eval.path_dependence > 40:
        diags.append({
            "severity": "medium",
            "code": "diag.path_dependent",
            "explanation": f"Path dependence {risk_eval.path_dependence:.0f}: merge order matters",
            "recommendation": "Coordinate merge timing with related intents",
        })

    # Bomb diagnostics
    for bomb in risk_eval.bombs:
        diags.append({
            "severity": bomb.get("severity", "high"),
            "code": f"diag.bomb.{bomb['type']}",
            "explanation": bomb.get("message", ""),
            "recommendation": {
                "cascade": "Split change to avoid touching high-centrality files simultaneously",
                "spiral": "Break circular dependencies before merging",
                "thermal_death": "System is under stress — reduce change scope immediately",
            }.get(bomb["type"], "Review and reduce change scope"),
        })

    for finding in risk_eval.findings:
        diags.append({
            "severity": finding.get("severity", "medium"),
            "code": finding.get("code", "diag.finding"),
            "explanation": finding.get("message", ""),
            "recommendation": "",
        })

    diags.sort(key=lambda d: _SEVERITY_ORDER.get(d["severity"], 3))
    return diags


# ---------------------------------------------------------------------------
# Full risk evaluation
# ---------------------------------------------------------------------------

def evaluate_risk(
    intent: Intent,
    simulation: Simulation,
    coupling_data: list[dict[str, Any]] | None = None,
) -> RiskEval:
    """Full risk evaluation: graph + 4 signals + bombs + legacy scores."""
    # Build dependency graph
    G = build_dependency_graph(intent, simulation, coupling_data)
    gm = graph_metrics(G)

    # Compute 4 independent signals
    ce = compute_entropic_load(intent, simulation, G)
    vc = compute_contextual_value(intent, simulation, G)
    dc = compute_complexity_delta(intent, simulation, G)
    pd = compute_path_dependence(intent, simulation, G)

    # Legacy scores (for backwards compat with policy gates)
    findings = analyze_findings(intent, simulation)
    edges = build_impact_edges(intent, simulation)
    prop = propagation_score(G, edges)
    cont = containment_score(intent, G, edges)

    # Detect bombs
    bombs = detect_bombs(intent, simulation, G)

    # Composite risk_score derived from 4 signals (weighted average)
    risk_score = min(100.0, round(
        ce * 0.30 +
        vc * 0.25 +
        dc * 0.20 +
        pd * 0.25,
        1,
    ))

    # entropy_score and damage_score for backwards compat
    entropy_score = ce  # entropic_load is the entropy signal
    damage_score = min(100.0, round(vc * 0.5 + ce * 0.3 + pd * 0.2, 1))

    return RiskEval(
        intent_id=intent.id,
        risk_score=risk_score,
        damage_score=damage_score,
        entropy_score=entropy_score,
        propagation_score=prop,
        containment_score=cont,
        entropic_load=ce,
        contextual_value=vc,
        complexity_delta=dc,
        path_dependence=pd,
        findings=findings,
        impact_edges=edges,
        graph_metrics=gm,
        bombs=bombs,
        tenant_id=intent.tenant_id,
    )
