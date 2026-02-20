"""Full risk evaluation: evaluate_risk, analyze_findings, build_diagnostics."""

from __future__ import annotations

from typing import Any

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
from converge.risk.signals import (
    compute_complexity_delta,
    compute_contextual_value,
    compute_entropic_load,
    compute_path_dependence,
)


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
