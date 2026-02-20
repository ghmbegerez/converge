"""Bomb detection: structural degradation patterns (cascade, spiral, thermal_death)."""

from __future__ import annotations

from typing import Any

import networkx as nx

from converge.models import Intent, Simulation


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
    except Exception:  # noqa: BLE001 — cap cycle enumeration on any graph error
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
