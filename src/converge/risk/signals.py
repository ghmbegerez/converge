"""4 independent risk signals: entropic load, contextual value, complexity delta, path dependence."""

from __future__ import annotations

from pathlib import PurePosixPath

import networkx as nx

from converge.models import Intent, Simulation
from converge.risk._constants import _CORE_PATHS, _CORE_TARGETS, _RISK_BONUS

# --- Entropic load weights ---
_EL_FILES = 2.0
_EL_CONFLICTS = 15.0
_EL_DEPS = 6.0
_EL_DIR_SPREAD = 3.0
_EL_COMPONENTS = 5.0

# --- Contextual value weights ---
_CV_IMPORTANCE_SCALE = 30.0
_CV_IMPORTANCE_CAP = 60.0
_CV_CORE_RATIO = 20.0
_CV_TARGET_BONUS = 10.0

# --- Complexity delta weights ---
_CD_DENSITY = 40.0
_CD_EDGE_RATIO_SCALE = 10.0
_CD_EDGE_RATIO_CAP = 30.0
_CD_CROSS_DIR = 3.0
_CD_SCOPE = 5.0

# --- Path dependence weights ---
_PD_CONFLICTS = 20.0
_PD_CORE_TOUCHES = 4.0
_PD_DEPS = 8.0
_PD_CYCLES = 5.0
_PD_LONGEST_PATH = 2.0
_PD_CYCLE_CAP = 20

_SCORE_MAX = 100.0
_SCORE_PRECISION = 1


def _clamp_score(raw: float) -> float:
    """Clamp a raw signal score to [0, 100] with standard precision."""
    return min(_SCORE_MAX, round(raw, _SCORE_PRECISION))


def _count_core_touches(simulation: Simulation) -> int:
    """Count files in core paths (high-contention areas)."""
    return sum(1 for f in simulation.files_changed
               if any(f.startswith(cp) for cp in _CORE_PATHS))


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
        files_count * _EL_FILES +
        conflict_count * _EL_CONFLICTS +
        deps_count * _EL_DEPS +
        dir_spread * _EL_DIR_SPREAD +
        (n_components - 1) * _EL_COMPONENTS
    )
    return _clamp_score(raw)


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
    core_touches = _count_core_touches(simulation)
    core_ratio = core_touches / max(len(simulation.files_changed), 1)

    # Target branch bonus
    target_bonus = _CV_TARGET_BONUS if intent.target in _CORE_TARGETS else 0.0

    raw = (
        min(importance_ratio * _CV_IMPORTANCE_SCALE, _CV_IMPORTANCE_CAP) +
        core_ratio * _CV_CORE_RATIO +
        target_bonus +
        _RISK_BONUS.get(intent.risk_level.value, 5)
    )
    return _clamp_score(raw)


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
        density * _CD_DENSITY +
        min(edge_node_ratio * _CD_EDGE_RATIO_SCALE, _CD_EDGE_RATIO_CAP) +
        cross_dir * _CD_CROSS_DIR +
        scope_count * _CD_SCOPE
    )
    return _clamp_score(raw)


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
    core_touches = _count_core_touches(simulation)

    # Cycles in the graph (circular dependencies) — cap enumeration
    cycle_count = 0
    try:
        if not nx.is_directed_acyclic_graph(G):
            for cycle in nx.simple_cycles(G):
                if len(cycle) >= 2:
                    cycle_count += 1
                if cycle_count >= _PD_CYCLE_CAP:
                    break
    except Exception:  # noqa: BLE001 — cap cycle enumeration on any graph error
        pass

    # Longest path in DAG (if acyclic)
    try:
        if nx.is_directed_acyclic_graph(G):
            longest = nx.dag_longest_path_length(G)
        else:
            longest = 0
    except Exception:  # noqa: BLE001 — degenerate graph fallback
        longest = 0

    raw = (
        conflict_count * _PD_CONFLICTS +
        core_touches * _PD_CORE_TOUCHES +
        deps_count * _PD_DEPS +
        cycle_count * _PD_CYCLES +
        longest * _PD_LONGEST_PATH
    )
    return _clamp_score(raw)
