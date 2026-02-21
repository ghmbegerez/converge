"""Dependency graph construction, metrics, impact edges, propagation and containment scores."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import networkx as nx

from converge.models import Intent, Simulation

# --- Edge weights ---
_WEIGHT_CONTAINMENT = 0.3       # file → parent directory
_WEIGHT_CO_LOCATED = 0.2        # files in same directory
_WEIGHT_SCOPE_CONTAINS = 0.5    # scope → matching file
_WEIGHT_SCOPE_TOUCHES = 0.2     # scope → non-matching file
_WEIGHT_DEPENDS_ON = 0.8        # intent → dependency
_WEIGHT_MERGE_TARGET = 1.0      # intent → target branch
_WEIGHT_CO_CHANGE_FACTOR = 0.1  # co_changes * factor (capped at 1.0)

# --- Propagation score scaling ---
_PROP_AVG_OUT_SCALE = 10.0      # avg out-degree multiplier
_PROP_GRAPH_CAP = 50.0          # max contribution from graph component
_PROP_WEIGHT_SCALE = 3.0        # edge weight sum multiplier
_PROP_TARGET_SCALE = 2.0        # unique targets multiplier
_PROP_EDGE_CAP = 50.0           # max contribution from edge component

# --- Containment score scaling ---
_CONT_CROSSING_PENALTY = 0.05   # penalty per boundary crossing
_CONT_COMPONENT_PENALTY = 0.03  # penalty per extra connected component

# --- Display limits ---
_PAGERANK_TOP_N = 10            # top PageRank entries retained
_PAGERANK_DISPLAY_LIMIT = 5     # PageRank entries shown in output
_IMPACT_FILES_LIMIT = 20        # max files in impact edges
_PAGERANK_PRECISION = 4         # decimal places for PageRank output
_CONTAINMENT_PRECISION = 2      # decimal places for containment score


def build_dependency_graph(
    intent: Intent,
    simulation: Simulation,
    coupling_data: list[dict[str, Any]] | None = None,
) -> nx.DiGraph:
    """Build a directed dependency graph from intent + simulation data.

    Nodes: files changed, directories, scopes, dependencies.
    Edges: file->dir (containment), file->file (coupling), scope->file, dep->intent.
    """
    G = nx.DiGraph()
    _add_file_and_directory_nodes(G, simulation)
    _add_proximity_coupling(G, simulation)
    _add_scope_edges(G, intent, simulation)
    _add_intent_and_dependency_edges(G, intent)
    if coupling_data:
        _add_external_coupling(G, simulation, coupling_data)
    return G


def _add_file_and_directory_nodes(G: nx.DiGraph, simulation: Simulation) -> None:
    """Add file nodes and directory containment edges."""
    dirs_seen: set[str] = set()
    for f in simulation.files_changed:
        G.add_node(f, kind="file", path=f)
        parts = PurePosixPath(f).parts
        if len(parts) > 1:
            parent = str(PurePosixPath(*parts[:-1]))
            if parent not in dirs_seen:
                G.add_node(parent, kind="directory")
                dirs_seen.add(parent)
            G.add_edge(f, parent, rel="contained_in", weight=_WEIGHT_CONTAINMENT)


def _add_proximity_coupling(G: nx.DiGraph, simulation: Simulation) -> None:
    """Connect files in the same directory with bidirectional co_located edges."""
    dir_files: dict[str, list[str]] = {}
    for f in simulation.files_changed:
        parts = PurePosixPath(f).parts
        parent = str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else "."
        dir_files.setdefault(parent, []).append(f)

    for files in dir_files.values():
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                if not G.has_edge(f1, f2):
                    G.add_edge(f1, f2, rel="co_located", weight=_WEIGHT_CO_LOCATED)
                if not G.has_edge(f2, f1):
                    G.add_edge(f2, f1, rel="co_located", weight=_WEIGHT_CO_LOCATED)


def _add_scope_edges(G: nx.DiGraph, intent: Intent, simulation: Simulation) -> None:
    """Add scope hint nodes and edges to files."""
    for scope in intent.technical.get("scope_hint", []):
        G.add_node(scope, kind="scope")
        for f in simulation.files_changed:
            if scope.lower() in f.lower():
                G.add_edge(scope, f, rel="scope_contains", weight=_WEIGHT_SCOPE_CONTAINS)
            else:
                G.add_edge(scope, f, rel="scope_touches", weight=_WEIGHT_SCOPE_TOUCHES)


def _add_intent_and_dependency_edges(G: nx.DiGraph, intent: Intent) -> None:
    """Add intent node, dependency nodes, and merge target edge."""
    for dep in intent.dependencies:
        G.add_node(dep, kind="dependency")
        G.add_edge(intent.id, dep, rel="depends_on", weight=_WEIGHT_DEPENDS_ON)

    G.add_node(intent.id, kind="intent")
    G.add_edge(intent.id, intent.target, rel="merge_target", weight=_WEIGHT_MERGE_TARGET)
    if intent.target not in G:
        G.add_node(intent.target, kind="branch")


def _add_external_coupling(
    G: nx.DiGraph, simulation: Simulation, coupling_data: list[dict[str, Any]],
) -> None:
    """Add co-change edges from archaeology coupling data."""
    changed_set = set(simulation.files_changed)
    for c in coupling_data:
        a, b = c.get("file_a", ""), c.get("file_b", "")
        co_changes = c.get("co_changes", 1)
        if a in changed_set or b in changed_set:
            w = min(1.0, co_changes * _WEIGHT_CO_CHANGE_FACTOR)
            if a not in G:
                G.add_node(a, kind="file", path=a)
            if b not in G:
                G.add_node(b, kind="file", path=b)
            G.add_edge(a, b, rel="co_change", weight=w)
            G.add_edge(b, a, rel="co_change", weight=w)


def graph_metrics(G: nx.DiGraph) -> dict[str, Any]:
    """Extract key metrics from the dependency graph."""
    if len(G) == 0:
        return {"nodes": 0, "edges": 0, "pagerank_max": 0.0, "pagerank_top": [],
                "components": 0, "density": 0.0}

    pr = nx.pagerank(G, weight="weight")
    top_pr = sorted(pr.items(), key=lambda x: -x[1])[:_PAGERANK_TOP_N]

    # Weakly connected components on undirected view
    n_components = nx.number_weakly_connected_components(G)

    # Density
    density = nx.density(G)

    # Find critical files (high PageRank, kind=file)
    critical_files = []
    for node, rank in top_pr:
        data = G.nodes.get(node, {})
        if data.get("kind") == "file":
            critical_files.append({"file": node, "pagerank": round(rank, _PAGERANK_PRECISION)})

    return {
        "nodes": len(G),
        "edges": G.number_of_edges(),
        "pagerank_max": round(top_pr[0][1], _PAGERANK_PRECISION) if top_pr else 0.0,
        "pagerank_top": [{"node": n, "rank": round(r, _PAGERANK_PRECISION)} for n, r in top_pr[:_PAGERANK_DISPLAY_LIMIT]],
        "critical_files": critical_files[:_PAGERANK_DISPLAY_LIMIT],
        "components": n_components,
        "density": round(density, _PAGERANK_PRECISION),
    }


def build_impact_edges(intent: Intent, simulation: Simulation) -> list[dict[str, Any]]:
    """Build directed impact edges (flat list for backwards compat)."""
    edges = []
    edges.append({"source": intent.source, "target": intent.target,
                  "type": "merge_target", "weight": _WEIGHT_MERGE_TARGET})
    for dep in intent.dependencies:
        edges.append({"source": intent.id, "target": dep,
                      "type": "depends_on", "weight": _WEIGHT_DEPENDS_ON})
    for scope in intent.technical.get("scope_hint", []):
        edges.append({"source": intent.id, "target": scope,
                      "type": "touches_scope", "weight": _WEIGHT_SCOPE_CONTAINS})
    for f in simulation.files_changed[:_IMPACT_FILES_LIMIT]:
        edges.append({"source": intent.id, "target": f,
                      "type": "modifies_file", "weight": _WEIGHT_CONTAINMENT})
    return edges


def propagation_score(G: nx.DiGraph, edges: list[dict[str, Any]]) -> float:
    """Score based on graph reachability and edge weights."""
    if len(G) == 0 and not edges:
        return 0.0

    # Graph-based: average out-degree of file nodes
    file_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "file"]
    if file_nodes:
        avg_out = sum(G.out_degree(n) for n in file_nodes) / len(file_nodes)
        graph_component = min(_PROP_GRAPH_CAP, avg_out * _PROP_AVG_OUT_SCALE)
    else:
        graph_component = 0.0

    # Edge-based (legacy): weight sum + unique targets
    total_weight = sum(e.get("weight", 0.5) for e in edges)
    unique_targets = len({e["target"] for e in edges})
    edge_component = min(_PROP_EDGE_CAP, total_weight * _PROP_WEIGHT_SCALE + unique_targets * _PROP_TARGET_SCALE)

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
    component_penalty = (n_components - 1) * _CONT_COMPONENT_PENALTY

    return round(max(0.0, 1.0 - (crossings * _CONT_CROSSING_PENALTY) - component_penalty), _CONTAINMENT_PRECISION)
