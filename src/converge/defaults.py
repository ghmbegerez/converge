"""Single source of truth for shared constants and configuration defaults.

Every magic number, threshold, or default that appears in more than one module
is defined here.  Domain-specific constants that are truly local to one module
(e.g. a regex pattern used only in one parser) stay in that module.

Invariant: ``grep -rn 'limit=10000\\|limit=10_000\\|limit=500' src/converge/``
should only show imports from this module or genuinely local overrides with a
comment explaining why.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Query limits
# ---------------------------------------------------------------------------

QUERY_LIMIT_SMALL = 200         # default for paginated list endpoints
QUERY_LIMIT_MEDIUM = 500        # analytics / summary aggregations
QUERY_LIMIT_LARGE = 10_000      # projections that need full dataset
QUERY_LIMIT_UNBOUNDED = 100_000  # audit chain, full reindex

# ---------------------------------------------------------------------------
# Queue processing
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
DEFAULT_TARGET_BRANCH = "main"
QUEUE_LOCK_TTL_SECONDS = 300

# ---------------------------------------------------------------------------
# Rollout hashing (deterministic bucketing for gradual enforcement)
# ---------------------------------------------------------------------------

ROLLOUT_HASH_CHARS = 8
ROLLOUT_DIVISOR = 0xFFFF_FFFF

# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------

CHECK_TIMEOUT_SECONDS = 300
CHECK_OUTPUT_LIMIT = 2000
CONFLICT_DISPLAY_LIMIT = 5

# ---------------------------------------------------------------------------
# Review SLA (hours by risk level value)
# ---------------------------------------------------------------------------

REVIEW_SLA_HOURS: dict[str, int] = {
    "low": 72,
    "medium": 48,
    "high": 24,
    "critical": 8,
}

# ---------------------------------------------------------------------------
# Intake thresholds
# ---------------------------------------------------------------------------

INTAKE_PAUSE_BELOW_HEALTH = 30.0
INTAKE_THROTTLE_BELOW_HEALTH = 60.0
INTAKE_THROTTLE_RATIO = 0.5

# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------

MAX_RISK_SCORE = 65.0
MAX_DAMAGE_SCORE = 60.0
MAX_PROPAGATION_SCORE = 55.0

# ---------------------------------------------------------------------------
# Calibration constants
# ---------------------------------------------------------------------------

CALIB_P75 = 0.75
CALIB_P90 = 0.90
CALIB_P95 = 0.95
CALIB_LOW_MULT = 1.5
CALIB_CRITICAL_MULT = 0.8
CALIB_FLOOR_LOW = 10.0
CALIB_FLOOR_MEDIUM = 8.0
CALIB_FLOOR_HIGH = 5.0
CALIB_FLOOR_CRITICAL = 3.0

# ---------------------------------------------------------------------------
# Security gate defaults per risk level
# ---------------------------------------------------------------------------

SECURITY_GATE_DEFAULTS: dict[str, dict[str, int]] = {
    "low":      {"max_critical": 0, "max_high": 5},
    "medium":   {"max_critical": 0, "max_high": 2},
    "high":     {"max_critical": 0, "max_high": 0},
    "critical": {"max_critical": 0, "max_high": 0},
}

# ---------------------------------------------------------------------------
# Coherence harness thresholds
# ---------------------------------------------------------------------------

COHERENCE_PASS_THRESHOLD = 75
COHERENCE_WARN_THRESHOLD = 60

# ---------------------------------------------------------------------------
# Policy profiles (embedded defaults, overridable via JSON)
# ---------------------------------------------------------------------------

DEFAULT_PROFILES: dict[str, dict] = {
    "low":      {"entropy_budget": 25.0, "containment_min": 0.3, "blast_limit": 50.0, "checks": ["lint"], "coherence_pass": 75, "coherence_warn": 60},
    "medium":   {"entropy_budget": 18.0, "containment_min": 0.5, "blast_limit": 35.0, "checks": ["lint"], "coherence_pass": 75, "coherence_warn": 60},
    "high":     {"entropy_budget": 12.0, "containment_min": 0.7, "blast_limit": 20.0, "checks": ["lint", "unit_tests"], "coherence_pass": 80, "coherence_warn": 65},
    "critical": {"entropy_budget":  6.0, "containment_min": 0.85, "blast_limit": 10.0, "checks": ["lint", "unit_tests"], "coherence_pass": 85, "coherence_warn": 70},
}

DEFAULT_RISK_THRESHOLDS: dict[str, float] = {
    "max_risk_score": MAX_RISK_SCORE,
    "max_damage_score": MAX_DAMAGE_SCORE,
    "max_propagation_score": MAX_PROPAGATION_SCORE,
}

DEFAULT_QUEUE_CONFIG: dict[str, object] = {
    "max_retries": MAX_RETRIES,
    "default_target": DEFAULT_TARGET_BRANCH,
}

# ---------------------------------------------------------------------------
# Risk gate checks (metric_key, threshold_key, default_value)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Risk classification thresholds (Initiative 2)
# ---------------------------------------------------------------------------

RISK_CLASSIFICATION_THRESHOLDS: dict[str, float] = {
    "low": 0.0,
    "medium": 25.0,
    "high": 50.0,
    "critical": 75.0,
}

RISK_GATE_CHECKS: list[tuple[str, str, float]] = [
    ("risk_score", "max_risk_score", MAX_RISK_SCORE),
    ("damage_score", "max_damage_score", MAX_DAMAGE_SCORE),
    ("propagation_score", "max_propagation_score", MAX_PROPAGATION_SCORE),
]
