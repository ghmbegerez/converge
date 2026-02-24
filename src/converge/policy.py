"""Policy engine: loads config, evaluates the 4 gates, manages risk policies.

Gates:
  1. Verification — required checks passed for the risk level.
  2. Containment — containment_score >= threshold.
  3. Entropy — entropy_delta within budget.
  4. Security — no critical/high findings above threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from converge.defaults import (
    CALIB_CRITICAL_MULT,
    CALIB_FLOOR_CRITICAL,
    CALIB_FLOOR_HIGH,
    CALIB_FLOOR_LOW,
    CALIB_FLOOR_MEDIUM,
    CALIB_LOW_MULT,
    CALIB_P75,
    CALIB_P90,
    CALIB_P95,
    DEFAULT_PROFILES,
    DEFAULT_QUEUE_CONFIG,
    DEFAULT_RISK_THRESHOLDS,
    RISK_GATE_CHECKS,
    ROLLOUT_DIVISOR,
    ROLLOUT_HASH_CHARS,
)
from converge.models import (
    GateName,
    GateResult,
    PolicyEvaluation,
    PolicyVerdict,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

@dataclass
class PolicyConfig:
    profiles: dict[str, dict[str, Any]]
    queue: dict[str, Any]
    risk: dict[str, Any]
    origin_overrides: dict[str, dict[str, dict[str, Any]]] | None = None

    def profile_for(
        self, risk_level: RiskLevel | str, origin_type: str | None = None,
    ) -> dict[str, Any]:
        key = risk_level.value if isinstance(risk_level, RiskLevel) else risk_level
        base = dict(self.profiles.get(key, self.profiles["medium"]))
        # Apply origin-specific overrides if present
        if origin_type and self.origin_overrides:
            origin_rules = self.origin_overrides.get(origin_type, {})
            overrides = origin_rules.get(key, origin_rules.get("_default", {}))
            if overrides:
                base.update(overrides)
        return base


def load_config(config_path: str | Path | None = None) -> PolicyConfig:
    profiles = dict(DEFAULT_PROFILES)
    queue = dict(DEFAULT_QUEUE_CONFIG)
    risk = dict(DEFAULT_RISK_THRESHOLDS)
    origin_overrides = None

    paths_to_try: list[Path] = []
    if config_path:
        paths_to_try.append(Path(config_path))
    paths_to_try.extend([
        Path(".converge/policy.json"),
        Path("policy.json"),
        Path("policy.default.json"),
    ])

    for p in paths_to_try:
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            if "profiles" in data:
                profiles.update(data["profiles"])
            if "queue" in data:
                queue.update(data["queue"])
            if "risk" in data:
                risk.update(data["risk"])
            if "origin_overrides" in data:
                origin_overrides = data["origin_overrides"]
            break

    return PolicyConfig(
        profiles=profiles, queue=queue, risk=risk,
        origin_overrides=origin_overrides,
    )


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate(
    *,
    risk_level: RiskLevel,
    checks_passed: list[str],
    entropy_delta: float,
    containment_score: float,
    security_findings: list[dict[str, Any]] | None = None,
    config: PolicyConfig | None = None,
    origin_type: str | None = None,
) -> PolicyEvaluation:
    """Evaluate the 4 policy gates. Returns ALLOW or BLOCK with gate details."""
    if config is None:
        config = load_config()

    profile = config.profile_for(risk_level, origin_type=origin_type)
    gates: list[GateResult] = []

    # Gate 1: Verification — required checks
    required_checks = profile.get("checks", [])
    missing = [c for c in required_checks if c not in checks_passed]
    gates.append(GateResult(
        gate=GateName.VERIFICATION,
        passed=len(missing) == 0,
        reason=f"Missing checks: {missing}" if missing else "All required checks passed",
        value=len(checks_passed),
        threshold=len(required_checks),
    ))

    # Gate 2: Containment
    containment_min = profile.get("containment_min", 0.5)
    gates.append(GateResult(
        gate=GateName.CONTAINMENT,
        passed=containment_score >= containment_min,
        reason=f"Containment {containment_score:.2f} vs min {containment_min:.2f}",
        value=containment_score,
        threshold=containment_min,
    ))

    # Gate 3: Entropy
    entropy_budget = profile.get("entropy_budget", 18.0)
    gates.append(GateResult(
        gate=GateName.ENTROPY,
        passed=entropy_delta <= entropy_budget,
        reason=f"Entropy delta {entropy_delta:.1f} vs budget {entropy_budget:.1f}",
        value=entropy_delta,
        threshold=entropy_budget,
    ))

    # Gate 4: Security — no critical/high findings above threshold
    if security_findings is not None:
        gates.append(_evaluate_security_gate(security_findings, profile))

    all_passed = all(g.passed for g in gates)
    return PolicyEvaluation(
        verdict=PolicyVerdict.ALLOW if all_passed else PolicyVerdict.BLOCK,
        gates=gates,
        risk_level=risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(risk_level),
        profile_used=risk_level.value if isinstance(risk_level, RiskLevel) else risk_level,
    )


def _evaluate_security_gate(
    findings: list[dict[str, Any]],
    profile: dict[str, Any],
) -> GateResult:
    """Evaluate the security gate based on finding severity counts."""
    critical = sum(1 for f in findings if f.get("severity") == "critical")
    high = sum(1 for f in findings if f.get("severity") == "high")

    security_cfg = profile.get("security", {})
    max_critical = security_cfg.get("max_critical", 0)
    max_high = security_cfg.get("max_high", 2)

    passed = critical <= max_critical and high <= max_high
    reason = (
        f"Security: {critical} critical, {high} high "
        f"(max critical={max_critical}, max high={max_high})"
    )
    return GateResult(
        gate=GateName.SECURITY,
        passed=passed,
        reason=reason,
        value=float(critical * 10 + high),
        threshold=float(max_critical * 10 + max_high),
    )


def _rollout_bucket(intent_id: str) -> float:
    """Deterministic rollout bucket [0.0, 1.0) for gradual enforcement.

    Uses hash of intent_id so the same intent always lands in the same bucket.
    This ensures consistent behavior across retries.
    """
    import hashlib
    h = hashlib.sha256(intent_id.encode()).hexdigest()[:ROLLOUT_HASH_CHARS]
    return int(h, 16) / ROLLOUT_DIVISOR


def evaluate_risk_gate(
    *,
    risk_score: float,
    damage_score: float,
    propagation_score: float,
    thresholds: dict[str, float] | None = None,
    mode: str = "shadow",
    enforce_ratio: float = 1.0,
    intent_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate risk thresholds with gradual enforcement (canary rollout).

    - shadow mode: logs would_block but never enforces
    - enforce mode with enforce_ratio < 1.0: only enforces for a fraction
      of intents (deterministic bucket by intent_id hash)
    - enforce mode with enforce_ratio = 1.0: enforces for all intents
    """
    t = thresholds or DEFAULT_RISK_THRESHOLDS
    scores = {"risk_score": risk_score, "damage_score": damage_score, "propagation_score": propagation_score}
    breaches = []
    for metric, threshold_key, default in RISK_GATE_CHECKS:
        value = scores[metric]
        limit = t.get(threshold_key, default)
        if value > limit:
            breaches.append({"metric": metric, "value": value, "limit": limit})

    would_block = len(breaches) > 0

    # Gradual enforcement via deterministic bucketing
    bucket = _rollout_bucket(intent_id) if intent_id else 0.0
    in_enforcement_group = bucket < enforce_ratio
    enforced = mode == "enforce" and would_block and in_enforcement_group

    return {
        "would_block": would_block,
        "enforced": enforced,
        "mode": mode,
        "enforce_ratio": enforce_ratio,
        "rollout_bucket": round(bucket, 4),
        "in_enforcement_group": in_enforcement_group,
        "breaches": breaches,
    }


# ---------------------------------------------------------------------------
# Calibration (data-driven threshold adjustment)
# ---------------------------------------------------------------------------

def calibrate_profiles(
    historical_scores: list[dict[str, float]],
    base_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Recalibrate profiles from historical risk/entropy data using quantiles."""
    profiles = {k: dict(v) for k, v in (base_profiles or DEFAULT_PROFILES).items()}
    if not historical_scores:
        return profiles

    entropy_vals = sorted(s.get("entropy_score", 0) for s in historical_scores)
    n = len(entropy_vals)
    p75 = entropy_vals[int(n * CALIB_P75)] if n > 0 else 18.0
    p90 = entropy_vals[int(n * CALIB_P90)] if n > 0 else 12.0
    p95 = entropy_vals[int(n * CALIB_P95)] if n > 0 else 6.0

    profiles["low"]["entropy_budget"] = round(max(p75 * CALIB_LOW_MULT, CALIB_FLOOR_LOW), 1)
    profiles["medium"]["entropy_budget"] = round(max(p75, CALIB_FLOOR_MEDIUM), 1)
    profiles["high"]["entropy_budget"] = round(max(p90, CALIB_FLOOR_HIGH), 1)
    profiles["critical"]["entropy_budget"] = round(max(p95 * CALIB_CRITICAL_MULT, CALIB_FLOOR_CRITICAL), 1)

    return profiles
