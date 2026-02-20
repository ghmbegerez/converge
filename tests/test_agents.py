"""Tests for agent authorization."""

from converge import agents, event_log
from converge.models import AgentPolicy, Event, Intent, RiskLevel, Status


def _setup_intent_with_risk(db_path, risk_score=30.0, damage_score=20.0):
    intent = Intent(id="agt-001", source="f/x", target="main",
                    status=Status.READY, risk_level=RiskLevel.MEDIUM, tenant_id="team-a")
    event_log.upsert_intent(db_path, intent)
    event_log.append(db_path, Event(
        event_type="risk.evaluated",
        intent_id="agt-001",
        tenant_id="team-a",
        payload={"risk_score": risk_score, "damage_score": damage_score,
                 "entropy_score": 10.0, "propagation_score": 15.0},
    ))
    # Seed a simulation so compliance can compute
    event_log.append(db_path, Event(
        event_type="simulation.completed",
        intent_id="agt-001",
        tenant_id="team-a",
        payload={"mergeable": True, "conflicts": [], "files_changed": ["a.py"],
                 "source": "f/x", "target": "main"},
    ))


class TestAgentAuthorization:
    def test_default_policy_blocks_merge(self, db_path):
        _setup_intent_with_risk(db_path)
        result = agents.authorize(
            db_path, agent_id="bot-1", action="merge",
            intent_id="agt-001", tenant_id="team-a",
        )
        assert not result["allowed"]
        # "merge" not in default allow_actions ["analyze"]
        assert any("not in allowed" in r for r in result["reasons"])

    def test_configured_policy_allows(self, db_path):
        _setup_intent_with_risk(db_path, risk_score=20.0)
        pol = AgentPolicy(
            agent_id="bot-2",
            tenant_id="team-a",
            atl=2,
            max_risk_score=50.0,
            allow_actions=["analyze", "merge"],
            require_human_approval=False,
            require_dual_approval_on_critical=False,
        )
        agents.set_policy(db_path, pol)

        result = agents.authorize(
            db_path, agent_id="bot-2", action="merge",
            intent_id="agt-001", tenant_id="team-a",
        )
        assert result["allowed"]

    def test_risk_exceeds_agent_limit(self, db_path):
        _setup_intent_with_risk(db_path, risk_score=80.0)
        pol = AgentPolicy(
            agent_id="bot-3",
            tenant_id="team-a",
            max_risk_score=50.0,
            allow_actions=["merge"],
            require_human_approval=False,
        )
        agents.set_policy(db_path, pol)

        result = agents.authorize(
            db_path, agent_id="bot-3", action="merge",
            intent_id="agt-001", tenant_id="team-a",
        )
        assert not result["allowed"]
        assert any("Risk score" in r for r in result["reasons"])

    def test_human_approval_required(self, db_path):
        _setup_intent_with_risk(db_path, risk_score=10.0)
        pol = AgentPolicy(
            agent_id="bot-4",
            tenant_id="team-a",
            allow_actions=["merge"],
            require_human_approval=True,
        )
        agents.set_policy(db_path, pol)

        # Without approval
        result = agents.authorize(
            db_path, agent_id="bot-4", action="merge",
            intent_id="agt-001", tenant_id="team-a", human_approvals=0,
        )
        assert not result["allowed"]

        # With approval
        result = agents.authorize(
            db_path, agent_id="bot-4", action="merge",
            intent_id="agt-001", tenant_id="team-a", human_approvals=1,
        )
        assert result["allowed"]

    def test_action_overrides(self, db_path):
        _setup_intent_with_risk(db_path, risk_score=45.0)
        pol = AgentPolicy(
            agent_id="bot-5",
            tenant_id="team-a",
            max_risk_score=60.0,
            allow_actions=["analyze", "automerge"],
            action_overrides={"automerge": {"max_risk_score": 30.0}},
            require_human_approval=False,
        )
        agents.set_policy(db_path, pol)

        # Default action: within limits
        result = agents.authorize(
            db_path, agent_id="bot-5", action="analyze",
            intent_id="agt-001", tenant_id="team-a",
        )
        assert result["allowed"]

        # Automerge: override limit is stricter
        result = agents.authorize(
            db_path, agent_id="bot-5", action="automerge",
            intent_id="agt-001", tenant_id="team-a",
        )
        assert not result["allowed"]

    def test_authorization_event_recorded(self, db_path):
        _setup_intent_with_risk(db_path)
        agents.authorize(
            db_path, agent_id="bot-6", action="analyze",
            intent_id="agt-001", tenant_id="team-a",
        )
        events = event_log.query(db_path, event_type="agent.authorized")
        assert len(events) >= 1


class TestPolicyCRUD:
    def test_set_and_get(self, db_path):
        pol = AgentPolicy(agent_id="crud-bot", tenant_id="team-a", atl=3, allow_actions=["merge"])
        agents.set_policy(db_path, pol)
        loaded = agents.get_policy(db_path, "crud-bot", "team-a")
        assert loaded.atl == 3
        assert "merge" in loaded.allow_actions

    def test_list_policies(self, db_path):
        agents.set_policy(db_path, AgentPolicy(agent_id="b1", allow_actions=["a"]))
        agents.set_policy(db_path, AgentPolicy(agent_id="b2", allow_actions=["b"]))
        policies = agents.list_policies(db_path)
        assert len(policies) >= 2
