"""Projections: derived views over the event log.

All projections are computed from events. If corrupted, they can be
rebuilt from the event log (source of truth).

Modules:
  - health: repo health, change health, predictive health gate
  - compliance: SLO/KPI evaluation
  - trends: risk, entropy, health time series + integration metrics
  - predictions: issue detection from trends + bomb signals
  - queue: queue state + agent performance
  - learning: structured actionable lessons
"""

# Re-export all public functions for backward compatibility.
# Consumers can use `from converge import projections; projections.repo_health(...)`
# or import directly: `from converge.projections.health import repo_health`

from converge.projections.health import (
    change_health,
    predict_health,
    repo_health,
)
from converge.projections.compliance import (
    DEFAULT_THRESHOLDS,
    compliance_report,
)
from converge.projections.trends import (
    change_health_trend,
    entropy_trend,
    health_trend,
    integration_metrics,
    risk_trend,
)
from converge.projections.predictions import predict_issues
from converge.projections.queue import agent_performance, queue_state
from converge.projections.verification import verification_debt
from converge.projections.learning import (
    derive_change_learning,
    derive_health_learning,
)

__all__ = [
    # Health
    "repo_health",
    "change_health",
    "predict_health",
    # Compliance
    "DEFAULT_THRESHOLDS",
    "compliance_report",
    # Trends
    "risk_trend",
    "entropy_trend",
    "health_trend",
    "change_health_trend",
    "integration_metrics",
    # Predictions
    "predict_issues",
    # Queue
    "queue_state",
    "agent_performance",
    # Verification debt
    "verification_debt",
    # Learning
    "derive_health_learning",
    "derive_change_learning",
]
