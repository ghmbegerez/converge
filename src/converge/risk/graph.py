"""Dependency graph construction, metrics, impact edges, propagation and containment scores."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import networkx as nx

from converge.models import Intent, Simulation


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

    # Add file nodes with attributes
    for f in simulation.files_changed:
        G.add_node(f, kind="file", path=f)

    # Directory containment edges (file -> parent dir)
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
