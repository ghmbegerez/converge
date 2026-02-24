"""Agent authorization: policy CRUD and action authorization.

Agents are autonomous actors (CI bots, AI assistants, etc.) that can
interact with the system. Each agent has a policy defining:
  - ATL (Autonomy Trust Level, 0-3)
  - Risk limits (max_risk_score, max_blast_severity)
  - Required approvals (human, dual for critical)
  - Allowed actions with optional per-action overrides
  - Expiration
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from converge import event_log, projections
from converge.models import AgentPolicy, Event, EventType, now_iso

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

DEFAULT_POLICY = AgentPolicy(
    agent_id="default",
    atl=0,
    max_risk_score=30.0,
    max_blast_severity="low",
    require_compliance_pass=True,
    require_human_approval=True,
    require_dual_approval_on_critical=True,
    allow_actions=["analyze"],
)


def get_policy(agent_id: str, tenant_id: str | None = None) -> AgentPolicy:
    data = event_log.get_agent_policy(agent_id, tenant_id)
    if data is None:
        return AgentPolicy(agent_id=agent_id, tenant_id=tenant_id)
    return AgentPolicy.from_dict(data)


def set_policy(policy: AgentPolicy) -> dict[str, Any]:
    event_log.upsert_agent_policy(policy.to_dict())
    event_log.append(Event(
        event_type=EventType.AGENT_POLICY_UPDATED,
        agent_id=policy.agent_id,
        tenant_id=policy.tenant_id,
        payload=policy.to_dict(),
    ))
    return policy.to_dict()


def list_policies(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return event_log.list_agent_policies(tenant_id=tenant_id)


def authorize(
    *,
    agent_id: str,
    action: str,
    intent_id: str,
    tenant_id: str | None = None,
    human_approvals: int = 0,
) -> dict[str, Any]:
    """Evaluate if an agent can execute an action on an intent."""
    pol = get_policy(agent_id, tenant_id)
    intent = event_log.get_intent(intent_id)
    reasons: list[str] = []
    allowed = True

    # Check expiration
    if pol.expires_at:
        try:
            exp = datetime.fromisoformat(pol.expires_at)
            if datetime.now(timezone.utc) > exp:
                reasons.append(f"Policy expired at {pol.expires_at}")
                allowed = False
        except ValueError:
            pass

    # Check action allowed
    effective_limits = dict(
        max_risk_score=pol.max_risk_score,
        max_blast_severity=pol.max_blast_severity,
        min_test_coverage=pol.min_test_coverage,
    )
    # Apply action-specific overrides
    if action in pol.action_overrides:
        effective_limits.update(pol.action_overrides[action])

    if action not in pol.allow_actions:
        reasons.append(f"Action '{action}' not in allowed actions: {pol.allow_actions}")
        allowed = False

    # Check risk
    if intent:
        risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, intent_id=intent_id, limit=1)
        if risk_events:
            risk_score = risk_events[0]["payload"].get("risk_score", 0)
            if risk_score > effective_limits["max_risk_score"]:
                reasons.append(f"Risk score {risk_score:.0f} > agent limit {effective_limits['max_risk_score']}")
                allowed = False

            # Blast severity check
            damage = risk_events[0]["payload"].get("damage_score", 0)
            actual_severity = "low" if damage < 30 else ("medium" if damage < 50 else ("high" if damage < 75 else "critical"))
            max_sev = effective_limits.get("max_blast_severity", pol.max_blast_severity)
            if _SEVERITY_RANK.get(actual_severity, 0) > _SEVERITY_RANK.get(max_sev, 0):
                reasons.append(f"Blast severity '{actual_severity}' exceeds agent limit '{max_sev}'")
                allowed = False

        # Compliance check
        if pol.require_compliance_pass:
            compliance = projections.compliance_report(tenant_id=tenant_id)
            if not compliance.passed:
                reasons.append("Compliance check not passing")
                allowed = False

    # Human approval check
    if pol.require_human_approval and human_approvals < 1:
        reasons.append("Human approval required but none provided")
        allowed = False

    # Dual approval on critical
    if intent and intent.risk_level.value == "critical" and pol.require_dual_approval_on_critical:
        if human_approvals < 2:
            reasons.append(f"Critical risk requires 2 approvals, got {human_approvals}")
            allowed = False

    result = {
        "agent_id": agent_id,
        "action": action,
        "intent_id": intent_id,
        "allowed": allowed,
        "reasons": reasons,
        "atl": pol.atl,
        "effective_limits": effective_limits,
        "human_approvals": human_approvals,
        "timestamp": now_iso(),
    }

    event_log.append(Event(
        event_type=EventType.AGENT_AUTHORIZED,
        agent_id=agent_id,
        intent_id=intent_id,
        tenant_id=tenant_id,
        payload=result,
        evidence={"allowed": allowed, "action": action},
    ))

    return result
