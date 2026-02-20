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

from converge.risk.bombs import detect_bombs
from converge.risk.eval import analyze_findings, build_diagnostics, evaluate_risk
from converge.risk.graph import (
    build_dependency_graph,
    build_impact_edges,
    containment_score,
    graph_metrics,
    propagation_score,
)
from converge.risk.signals import (
    compute_complexity_delta,
    compute_contextual_value,
    compute_entropic_load,
    compute_path_dependence,
)

__all__ = [
    "analyze_findings",
    "build_dependency_graph",
    "build_diagnostics",
    "build_impact_edges",
    "compute_complexity_delta",
    "compute_contextual_value",
    "compute_entropic_load",
    "compute_path_dependence",
    "containment_score",
    "detect_bombs",
    "evaluate_risk",
    "graph_metrics",
    "propagation_score",
]
