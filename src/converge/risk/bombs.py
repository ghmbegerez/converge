"""Bomb detection: structural degradation patterns (cascade, spiral, thermal_death)."""

from __future__ import annotations

from typing import Any

import networkx as nx

from converge.models import Intent, Simulation

# --- Cascade detection thresholds ---
_CASCADE_PR_FACTOR = 1.5        # PageRank threshold = factor / max(len(G), 1)
_CASCADE_MIN_FANOUT = 3         # minimum out-degree to qualify as high-fanout
_CASCADE_BLAST_FACTOR = 1.5     # affected > files_changed * factor triggers bomb

# --- Spiral detection thresholds ---
_SPIRAL_MIN_CYCLE_LEN = 2       # minimum nodes in a cycle to be significant
_SPIRAL_MAX_CYCLES = 10         # cap cycle enumeration for performance
_SPIRAL_MIN_SIGNIFICANT = 2     # minimum significant cycles to trigger bomb

# --- Thermal death thresholds ---
_THERMAL_FILES_HOT = 10         # files_changed > this → hot indicator
_THERMAL_DEPS_HOT = 3           # dependencies > this → hot indicator
_THERMAL_COMPONENTS_HOT = 3     # weakly connected components > this → hot indicator
_THERMAL_EDGE_DENSITY_FACTOR = 2  # edges > nodes * factor → hot indicator
_THERMAL_MIN_INDICATORS = 3     # minimum hot indicators to trigger bomb

# --- Display limits ---
_CASCADE_DISPLAY_LIMIT = 5      # max trigger nodes shown
_CYCLE_DISPLAY_LIMIT = 3        # max cycles shown
_CYCLE_NODE_LIMIT = 5           # max nodes per cycle shown


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
    bombs: list[dict[str, Any]] = []

    if len(G) == 0:
        return bombs

    pr = nx.pagerank(G, weight="weight")

    _detect_cascade(simulation, G, pr, bombs)
    _detect_spiral(G, bombs)
    _detect_thermal_death(intent, simulation, G, bombs)

    return bombs


def _detect_cascade(
    simulation: Simulation,
    G: nx.DiGraph,
    pr: dict[str, float],
    out: list[dict[str, Any]],
) -> None:
    """Detect cascade bomb: high-centrality nodes with high fan-out."""
    file_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "file"]
    high_pr_files = [f for f in file_nodes
                     if pr.get(f, 0) > _CASCADE_PR_FACTOR / max(len(G), 1)]
    high_fanout = [f for f in high_pr_files if G.out_degree(f) >= _CASCADE_MIN_FANOUT]

    if high_fanout:
        affected = set()
        for f in high_fanout:
            affected.update(nx.descendants(G, f))
        if len(affected) > len(simulation.files_changed) * _CASCADE_BLAST_FACTOR:
            out.append({
                "type": "cascade",
                "severity": "high",
                "message": f"Change touches {len(high_fanout)} high-centrality node(s) "
                           f"with potential cascade to {len(affected)} nodes",
                "trigger_nodes": high_fanout[:_CASCADE_DISPLAY_LIMIT],
                "blast_radius": len(affected),
            })


def _detect_spiral(
    G: nx.DiGraph,
    out: list[dict[str, Any]],
) -> None:
    """Detect spiral bomb: circular dependencies in the graph."""
    try:
        significant_cycles = []
        if not nx.is_directed_acyclic_graph(G):
            for cycle in nx.simple_cycles(G):
                if len(cycle) >= _SPIRAL_MIN_CYCLE_LEN:
                    significant_cycles.append(cycle)
                if len(significant_cycles) >= _SPIRAL_MAX_CYCLES:
                    break
        if len(significant_cycles) >= _SPIRAL_MIN_SIGNIFICANT:
            out.append({
                "type": "spiral",
                "severity": "medium",
                "message": f"{len(significant_cycles)} circular dependency cycle(s) detected",
                "cycles": [c[:_CYCLE_NODE_LIMIT] for c in significant_cycles[:_CYCLE_DISPLAY_LIMIT]],
            })
    except Exception:  # noqa: BLE001 — cap cycle enumeration on any graph error
        pass


def _detect_thermal_death(
    intent: Intent,
    simulation: Simulation,
    G: nx.DiGraph,
    out: list[dict[str, Any]],
) -> None:
    """Detect thermal death bomb: multiple entropy indicators elevated simultaneously."""
    files_count = len(simulation.files_changed)
    conflict_count = len(simulation.conflicts)
    deps_count = len(intent.dependencies)
    n_components = nx.number_weakly_connected_components(G)

    hot_indicators = sum([
        files_count > _THERMAL_FILES_HOT,
        conflict_count > 0,
        deps_count > _THERMAL_DEPS_HOT,
        n_components > _THERMAL_COMPONENTS_HOT,
        len(G.edges()) > len(G.nodes()) * _THERMAL_EDGE_DENSITY_FACTOR,
    ])

    if hot_indicators >= _THERMAL_MIN_INDICATORS:
        out.append({
            "type": "thermal_death",
            "severity": "critical",
            "message": f"{hot_indicators}/5 entropy indicators elevated: "
                       f"files={files_count}, conflicts={conflict_count}, "
                       f"deps={deps_count}, components={n_components}, "
                       f"edge_density={G.number_of_edges()}/{len(G)}",
            "indicators": hot_indicators,
        })
