"""Policy engine: loads config, evaluates the 3 gates, manages risk policies.

Gates:
  1. Verification — required checks passed for the risk level.
  2. Containment — containment_score >= threshold.
  3. Entropy — entropy_delta within budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from converge.models import (
    GateName,
    GateResult,
    PolicyEvaluation,
    PolicyVerdict,
    RiskLevel,
)

# ---------------------------------------------------------------------------
# Default profiles (embedded, overridable via JSON)
# ---------------------------------------------------------------------------

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "low":      {"entropy_budget": 25.0, "containment_min": 0.3, "blast_limit": 50.0, "checks": ["lint"]},
    "medium":   {"entropy_budget": 18.0, "containment_min": 0.5, "blast_limit": 35.0, "checks": ["lint"]},
    "high":     {"entropy_budget": 12.0, "containment_min": 0.7, "blast_limit": 20.0, "checks": ["lint", "unit_tests"]},
    "critical": {"entropy_budget":  6.0, "containment_min": 0.85, "blast_limit": 10.0, "checks": ["lint", "unit_tests"]},
}

DEFAULT_RISK_THRESHOLDS: dict[str, float] = {
    "max_risk_score": 65.0,
    "max_damage_score": 60.0,
    "max_propagation_score": 55.0,
}

DEFAULT_QUEUE: dict[str, Any] = {
    "max_retries": 3,
    "default_target": "main",
}


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

@dataclass
class PolicyConfig:
    profiles: dict[str, dict[str, Any]]
    queue: dict[str, Any]
    risk: dict[str, Any]

    def profile_for(self, risk_level: RiskLevel | str) -> dict[str, Any]:
        key = risk_level.value if isinstance(risk_level, RiskLevel) else risk_level
        return self.profiles.get(key, self.profiles["medium"])


def load_config(config_path: str | Path | None = None) -> PolicyConfig:
    profiles = dict(DEFAULT_PROFILES)
    queue = dict(DEFAULT_QUEUE)
    risk = dict(DEFAULT_RISK_THRESHOLDS)

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
            break

    return PolicyConfig(profiles=profiles, queue=queue, risk=risk)


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate(
    *,
    risk_level: RiskLevel,
    checks_passed: list[str],
    entropy_delta: float,
    containment_score: float,
    config: PolicyConfig | None = None,
) -> PolicyEvaluation:
    """Evaluate the 3 policy gates. Returns ALLOW or BLOCK with gate details."""
    if config is None:
        config = load_config()

    profile = config.profile_for(risk_level)
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

    all_passed = all(g.passed for g in gates)
    return PolicyEvaluation(
        verdict=PolicyVerdict.ALLOW if all_passed else PolicyVerdict.BLOCK,
        gates=gates,
        risk_level=risk_level,
        profile_used=risk_level.value if isinstance(risk_level, RiskLevel) else risk_level,
    )


def _rollout_bucket(intent_id: str) -> float:
    """Deterministic rollout bucket [0.0, 1.0) for gradual enforcement.

    Uses hash of intent_id so the same intent always lands in the same bucket.
    This ensures consistent behavior across retries.
    """
    import hashlib
    h = hashlib.sha256(intent_id.encode()).hexdigest()[:8]
    return int(h, 16) / 0xFFFFFFFF


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
    breaches = []
    if risk_score > t.get("max_risk_score", 65.0):
        breaches.append({"metric": "risk_score", "value": risk_score, "limit": t["max_risk_score"]})
    if damage_score > t.get("max_damage_score", 60.0):
        breaches.append({"metric": "damage_score", "value": damage_score, "limit": t["max_damage_score"]})
    if propagation_score > t.get("max_propagation_score", 55.0):
        breaches.append({"metric": "propagation_score", "value": propagation_score, "limit": t["max_propagation_score"]})

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
    p75 = entropy_vals[int(n * 0.75)] if n > 0 else 18.0
    p90 = entropy_vals[int(n * 0.90)] if n > 0 else 12.0
    p95 = entropy_vals[int(n * 0.95)] if n > 0 else 6.0

    profiles["low"]["entropy_budget"] = round(max(p75 * 1.5, 10.0), 1)
    profiles["medium"]["entropy_budget"] = round(max(p75, 8.0), 1)
    profiles["high"]["entropy_budget"] = round(max(p90, 5.0), 1)
    profiles["critical"]["entropy_budget"] = round(max(p95 * 0.8, 3.0), 1)

    return profiles
