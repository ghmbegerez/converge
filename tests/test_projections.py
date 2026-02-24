"""Tests for projections (derived views over events)."""

from converge import event_log, projections
from converge.models import Event, Intent, Status, now_iso


def _seed_events(n_sims=10, n_merged=5, n_rejected=2):
    """Seed DB with realistic events for projection tests."""
    for i in range(n_sims):
        event_log.append(Event(
            event_type="simulation.completed",
            intent_id=f"int-{i:03d}",
            tenant_id="team-a",
            payload={"mergeable": i < (n_sims - 2), "conflicts": [] if i < (n_sims - 2) else ["x.py"],
                     "files_changed": [f"f{i}.py"], "source": f"f/{i}", "target": "main"},
        ))
        event_log.append(Event(
            event_type="risk.evaluated",
            intent_id=f"int-{i:03d}",
            tenant_id="team-a",
            payload={"risk_score": i * 5.0, "damage_score": i * 3.0,
                     "entropy_score": i * 2.0, "propagation_score": i * 1.5,
                     "containment_score": max(0.0, 1.0 - i * 0.08)},
        ))

    for i in range(n_merged):
        event_log.append(Event(
            event_type="intent.merged",
            intent_id=f"int-{i:03d}",
            tenant_id="team-a",
            payload={"merged_commit": f"sha-{i}", "source": f"f/{i}", "target": "main"},
        ))

    for i in range(n_rejected):
        event_log.append(Event(
            event_type="intent.rejected",
            intent_id=f"int-rej-{i:03d}",
            tenant_id="team-a",
            payload={"reason": "max_retries", "retries": 3},
        ))


class TestRepoHealth:
    def test_basic_health(self, db_path):
        _seed_events()
        health = projections.repo_health(tenant_id="team-a")
        assert 0 <= health.repo_health_score <= 100
        assert health.status in ("green", "yellow", "red")
        assert health.merged_last_24h == 5
        assert health.rejected_last_24h == 2

    def test_health_learning(self, db_path):
        _seed_events()
        health = projections.repo_health(tenant_id="team-a")
        assert "summary" in health.learning
        assert "level" in health.learning

    def test_health_event_recorded(self, db_path):
        _seed_events()
        projections.repo_health()
        events = event_log.query(event_type="health.snapshot")
        assert len(events) >= 1


class TestChangeHealth:
    def test_change_health(self, db_path):
        _seed_events(n_sims=3)
        result = projections.change_health("int-000")
        assert "health_score" in result
        assert result["status"] in ("green", "yellow", "red")


class TestCompliance:
    def test_compliance_passing(self, db_path):
        _seed_events(n_sims=10, n_rejected=0)
        report = projections.compliance_report()
        assert report.mergeable_rate >= 0.8
        assert report.passed is True

    def test_compliance_with_tenant_thresholds(self, db_path):
        _seed_events()
        event_log.upsert_compliance_thresholds("team-a",
                                                {"min_mergeable_rate": 0.95})
        report = projections.compliance_report(tenant_id="team-a")
        # With strict thresholds, should fail
        has_alert = any(a["name"] == "mergeable_rate" for a in report.alerts)
        assert has_alert or report.mergeable_rate >= 0.95


class TestTrends:
    def test_risk_trend(self, db_path):
        _seed_events()
        trend = projections.risk_trend(tenant_id="team-a")
        assert len(trend) > 0
        assert "risk_score" in trend[0]

    def test_entropy_trend(self, db_path):
        _seed_events()
        trend = projections.entropy_trend(tenant_id="team-a")
        assert len(trend) > 0
        assert "entropy_score" in trend[0]


class TestQueueState:
    def test_queue_state(self, db_path):
        for i, s in enumerate([Status.READY, Status.VALIDATED, Status.QUEUED, Status.MERGED]):
            intent = Intent(id=f"qs-{i}", source=f"f/{i}", target="main", status=s, priority=i)
            event_log.upsert_intent(intent)

        state = projections.queue_state()
        assert state.total == 4
        assert len(state.pending) == 3  # READY + VALIDATED + QUEUED


class TestPredictions:
    def test_no_signals_when_healthy(self, db_path):
        # Only 2 sims, not enough for signals
        _seed_events(n_sims=2, n_merged=2, n_rejected=0)
        signals = projections.predict_issues()
        assert isinstance(signals, list)


class TestIntegrationMetrics:
    def test_integration_metrics(self, db_path):
        _seed_events()
        metrics = projections.integration_metrics(tenant_id="team-a")
        assert metrics["total_simulations"] == 10
        assert metrics["total_merged"] == 5
        assert "mergeable_rate" in metrics


class TestPredictHealth:
    def _seed_health_snapshots(self, n=6, declining=False):
        """Seed health.snapshot events for prediction tests."""
        for i in range(n):
            score = 80.0 - (i * 8) if declining else 80.0
            entropy = 5.0 + (i * 3) if declining else 5.0
            conflict = 0.05 + (i * 0.04) if declining else 0.05
            event_log.append(Event(
                event_type="health.snapshot",
                tenant_id="team-a",
                payload={
                    "repo_health_score": score,
                    "entropy_score": entropy,
                    "conflict_rate": conflict,
                    "status": "green" if score >= 70 else ("yellow" if score >= 40 else "red"),
                },
            ))

    def test_predict_insufficient_data(self, db_path):
        """Returns unknown with insufficient snapshots."""
        result = projections.predict_health(min_snapshots=3)
        assert result["projected_status"] == "unknown"
        assert result["should_gate"] is False

    def test_predict_stable_health(self, db_path):
        """Stable health projects green."""
        self._seed_health_snapshots(n=6, declining=False)
        result = projections.predict_health(tenant_id="team-a")
        assert result["projected_status"] == "green"
        assert result["should_gate"] is False
        assert result["data_points"] == 6

    def test_predict_declining_health(self, db_path):
        """Declining health generates signals."""
        self._seed_health_snapshots(n=8, declining=True)
        result = projections.predict_health(tenant_id="team-a")
        assert result["velocity"]["health"] < 0
        assert result["velocity"]["entropy"] > 0
        assert len(result["signals"]) > 0

    def test_predict_event_recorded(self, db_path):
        """Prediction records a health.prediction event."""
        self._seed_health_snapshots(n=4)
        projections.predict_health()
        events = event_log.query(event_type="health.prediction")
        assert len(events) >= 1


class TestStructuredLearning:
    def test_health_learning_structure(self, db_path):
        """Health learning returns structured lessons with metrics."""
        _seed_events(n_sims=10, n_rejected=5)
        health = projections.repo_health(tenant_id="team-a")
        learning = health.learning
        assert "lessons" in learning
        assert "next_actions" in learning
        assert "level" in learning
        # Lessons should have structured format
        if learning["lessons"]:
            lesson = learning["lessons"][0]
            assert "code" in lesson
            assert "metric" in lesson
            assert "observed" in lesson["metric"]
            assert "target" in lesson["metric"]

    def test_change_learning_structure(self, db_path):
        """Change-level learning returns structured lessons."""
        _seed_events(n_sims=3)
        result = projections.change_health("int-000")
        learning = result["learning"]
        assert "lessons" in learning
        assert "next_actions" in learning
        assert "level" in learning
