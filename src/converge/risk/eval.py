"""Full risk evaluation: evaluate_risk, analyze_findings, build_diagnostics."""

from __future__ import annotations

from typing import Any

from converge.defaults import CONFLICT_DISPLAY_LIMIT
from converge.models import Intent, RiskEval, Simulation
from converge.risk._constants import _CORE_TARGETS, _SEVERITY_ORDER
from converge.risk.bombs import detect_bombs
from converge.risk.graph import (
    build_dependency_graph,
    build_impact_edges,
    containment_score,
    graph_metrics,
    propagation_score,
)
# --- Risk score composite weights ---
_RISK_W_ENTROPIC = 0.30
_RISK_W_CONTEXTUAL = 0.25
_RISK_W_COMPLEXITY = 0.20
_RISK_W_PATH_DEP = 0.25
# --- Damage score weights ---
_DMG_W_CONTEXTUAL = 0.5
_DMG_W_ENTROPIC = 0.3
_DMG_W_PATH_DEP = 0.2
# --- Diagnostic thresholds ---
_DIAG_RISK_HIGH = 60
_DIAG_RISK_CRITICAL = 80
_DIAG_ENTROPY_MED = 20
_DIAG_ENTROPY_HIGH = 40
_DIAG_PROPAGATION = 40
_DIAG_CONTAINMENT = 0.4
_DIAG_ENTROPIC_LOAD = 50
_DIAG_CONTEXTUAL_VALUE = 60
_DIAG_PATH_DEP = 40
# --- Findings thresholds ---
_FINDING_LARGE_CHANGE = 15
_FINDING_DEP_SPREAD = 3

from converge.defaults import RISK_CLASSIFICATION_THRESHOLDS
from converge.models import RiskLevel

from converge.risk.signals import (
    compute_complexity_delta,
    compute_contextual_value,
    compute_entropic_load,
    compute_path_dependence,
)


def classify_risk_level(
    risk_score: float,
    thresholds: dict[str, float] | None = None,
) -> RiskLevel:
    """Classify risk level from composite score."""
    t = thresholds or RISK_CLASSIFICATION_THRESHOLDS
    if risk_score >= t["critical"]:
        return RiskLevel.CRITICAL
    if risk_score >= t["high"]:
        return RiskLevel.HIGH
    if risk_score >= t["medium"]:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def analyze_findings(intent: Intent, simulation: Simulation) -> list[dict[str, Any]]:
    """Generate specific findings from intent + simulation."""
    findings = []
    files_count = len(simulation.files_changed)
    deps_count = len(intent.dependencies)
    conflict_count = len(simulation.conflicts)

    if files_count > _FINDING_LARGE_CHANGE:
        findings.append({"code": "semantic.large_change", "severity": "high",
                         "message": f"Change touches {files_count} files"})
    if deps_count > _FINDING_DEP_SPREAD:
        findings.append({"code": "semantic.dependency_spread", "severity": "medium",
                         "message": f"Depends on {deps_count} other intents"})
    if intent.target in _CORE_TARGETS:
        findings.append({"code": "semantic.core_target", "severity": "high",
                         "message": f"Targets core branch: {intent.target}"})
    if conflict_count > 0:
        findings.append({"code": "semantic.merge_conflict", "severity": "critical",
                         "message": f"{conflict_count} merge conflict(s) detected"})

    return findings


_BOMB_RECOMMENDATIONS = {
    "cascade": "Split change to avoid touching high-centrality files simultaneously",
    "spiral": "Break circular dependencies before merging",
    "thermal_death": "System is under stress — reduce change scope immediately",
}


# --- Threshold-based diagnostic rules ---
# (field, op, threshold, code, base_severity, explanation_fmt, recommendation,
#  escalation_threshold, escalation_severity)
# explanation_fmt uses {value} placeholder. op is ">" or "<".
_THRESHOLD_DIAGS: list[tuple[str, str, float, str, str, str, str, float | None, str | None]] = [
    ("risk_score", ">", _DIAG_RISK_HIGH, "diag.high_risk", "high",
     "Combined risk score {value:.0f} exceeds safe threshold",
     "Split this change into smaller, independent intents",
     _DIAG_RISK_CRITICAL, "critical"),
    ("entropy_score", ">", _DIAG_ENTROPY_MED, "diag.high_entropy", "medium",
     "Entropy score {value:.0f} indicates high disorder",
     "Reduce file count or dependencies before merging",
     _DIAG_ENTROPY_HIGH, "high"),
    ("propagation_score", ">", _DIAG_PROPAGATION, "diag.high_propagation", "high",
     "Change propagation score {value:.0f} indicates wide blast radius",
     "Review impact graph and consider narrowing scope",
     None, None),
    ("containment_score", "<", _DIAG_CONTAINMENT, "diag.low_containment", "medium",
     "Containment {value:.2f} is below acceptable levels",
     "Add scope hints or reduce cross-boundary dependencies",
     None, None),
    ("entropic_load", ">", _DIAG_ENTROPIC_LOAD, "diag.high_entropic_load", "high",
     "Entropic load {value:.0f} indicates high disorder introduction",
     "Reduce the number of files, directories, or dependencies touched",
     None, None),
    ("contextual_value", ">", _DIAG_CONTEXTUAL_VALUE, "diag.high_contextual_value", "high",
     "Change touches critical files (contextual value: {value:.0f})",
     "Ensure thorough review — these files have high centrality in the codebase",
     None, None),
    ("path_dependence", ">", _DIAG_PATH_DEP, "diag.path_dependent", "medium",
     "Path dependence {value:.0f}: merge order matters",
     "Coordinate merge timing with related intents",
     None, None),
]


def build_diagnostics(
    intent: Intent,
    risk_eval: RiskEval,
    simulation: Simulation,
) -> list[dict[str, Any]]:
    """Generate explanatory diagnostics from risk evaluation."""
    diags: list[dict[str, Any]] = []
    _apply_threshold_diags(risk_eval, diags)
    _diag_merge_conflict(simulation, diags)
    _diag_bombs(risk_eval, diags)
    _diag_findings(risk_eval, diags)
    diags.sort(key=lambda d: _SEVERITY_ORDER.get(d["severity"], 3))
    return diags


def _apply_threshold_diags(re: RiskEval, out: list[dict[str, Any]]) -> None:
    """Apply all threshold-based diagnostic rules from _THRESHOLD_DIAGS."""
    for field, op, threshold, code, base_sev, expl_fmt, rec, esc_thresh, esc_sev in _THRESHOLD_DIAGS:
        value = getattr(re, field, 0)
        triggered = (value > threshold) if op == ">" else (value < threshold)
        if not triggered:
            continue
        severity = base_sev
        if esc_thresh is not None and esc_sev is not None:
            escalated = (value > esc_thresh) if op == ">" else (value < esc_thresh)
            if escalated:
                severity = esc_sev
        out.append({
            "severity": severity,
            "code": code,
            "explanation": expl_fmt.format(value=value),
            "recommendation": rec,
        })


def _diag_merge_conflict(sim: Simulation, out: list[dict[str, Any]]) -> None:
    if not sim.mergeable:
        out.append({
            "severity": "critical",
            "code": "diag.merge_conflict",
            "explanation": f"Merge has {len(sim.conflicts)} conflict(s): {', '.join(sim.conflicts[:CONFLICT_DISPLAY_LIMIT])}",
            "recommendation": "Resolve conflicts in source branch before retrying",
        })


def _diag_bombs(re: RiskEval, out: list[dict[str, Any]]) -> None:
    for bomb in re.bombs:
        out.append({
            "severity": bomb.get("severity", "high"),
            "code": f"diag.bomb.{bomb['type']}",
            "explanation": bomb.get("message", ""),
            "recommendation": _BOMB_RECOMMENDATIONS.get(bomb["type"], "Review and reduce change scope"),
        })


def _diag_findings(re: RiskEval, out: list[dict[str, Any]]) -> None:
    for finding in re.findings:
        out.append({
            "severity": finding.get("severity", "medium"),
            "code": finding.get("code", "diag.finding"),
            "explanation": finding.get("message", ""),
            "recommendation": "",
        })


def _compute_composite_scores(
    ce: float, vc: float, dc: float, pd: float,
) -> tuple[float, float, float]:
    """Compute risk_score, entropy_score, damage_score from 4 signals."""
    risk_score = min(100.0, round(
        ce * _RISK_W_ENTROPIC +
        vc * _RISK_W_CONTEXTUAL +
        dc * _RISK_W_COMPLEXITY +
        pd * _RISK_W_PATH_DEP,
        1,
    ))
    entropy_score = ce  # entropic_load is the entropy signal
    damage_score = min(100.0, round(
        vc * _DMG_W_CONTEXTUAL + ce * _DMG_W_ENTROPIC + pd * _DMG_W_PATH_DEP, 1,
    ))
    return risk_score, entropy_score, damage_score


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

    # Composite scores from 4 signals
    risk_score, entropy_score, damage_score = _compute_composite_scores(ce, vc, dc, pd)

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
