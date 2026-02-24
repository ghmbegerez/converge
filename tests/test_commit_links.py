"""Tests for intent_commit_links schema and CRUD (AR-01)."""

from conftest import make_intent

from converge import event_log
from converge.models import Intent, RiskLevel, Status


class TestCommitLinkCRUD:
    """Basic CRUD for intent_commit_links."""

    def test_upsert_and_list(self, db_path):
        make_intent(id="cl-001")
        event_log.upsert_commit_link("cl-001", "org/repo", "abc123", "head")
        event_log.upsert_commit_link("cl-001", "org/repo", "def456", "base")

        links = event_log.list_commit_links("cl-001")
        assert len(links) == 2
        shas = {l["sha"] for l in links}
        assert shas == {"abc123", "def456"}

    def test_upsert_idempotent(self, db_path):
        make_intent(id="cl-002")
        event_log.upsert_commit_link("cl-002", "org/repo", "abc123", "head")
        event_log.upsert_commit_link("cl-002", "org/repo", "abc123", "head")

        links = event_log.list_commit_links("cl-002")
        assert len(links) == 1

    def test_same_sha_different_roles(self, db_path):
        make_intent(id="cl-003")
        event_log.upsert_commit_link("cl-003", "org/repo", "abc123", "head")
        event_log.upsert_commit_link("cl-003", "org/repo", "abc123", "merge")

        links = event_log.list_commit_links("cl-003")
        assert len(links) == 2
        roles = {l["role"] for l in links}
        assert roles == {"head", "merge"}

    def test_delete_link(self, db_path):
        make_intent(id="cl-004")
        event_log.upsert_commit_link("cl-004", "org/repo", "abc123", "head")

        deleted = event_log.delete_commit_link("cl-004", "abc123", "head")
        assert deleted is True

        links = event_log.list_commit_links("cl-004")
        assert len(links) == 0

    def test_delete_nonexistent_returns_false(self, db_path):
        deleted = event_log.delete_commit_link("cl-999", "xyz", "head")
        assert deleted is False

    def test_list_empty(self, db_path):
        links = event_log.list_commit_links("nonexistent")
        assert links == []

    def test_links_isolated_per_intent(self, db_path):
        make_intent(id="cl-005")
        make_intent(id="cl-006")
        event_log.upsert_commit_link("cl-005", "org/repo", "aaa", "head")
        event_log.upsert_commit_link("cl-006", "org/repo", "bbb", "head")

        links_5 = event_log.list_commit_links("cl-005")
        links_6 = event_log.list_commit_links("cl-006")
        assert len(links_5) == 1
        assert len(links_6) == 1
        assert links_5[0]["sha"] == "aaa"
        assert links_6[0]["sha"] == "bbb"

    def test_upsert_updates_observed_at(self, db_path):
        make_intent(id="cl-007")
        event_log.upsert_commit_link(
        "cl-007", "org/repo", "abc123", "head",
            observed_at="2026-01-01T00:00:00Z",
        )
        event_log.upsert_commit_link(
        "cl-007", "org/repo", "abc123", "head",
            observed_at="2026-02-01T00:00:00Z",
        )

        links = event_log.list_commit_links("cl-007")
        assert len(links) == 1
        assert links[0]["observed_at"] == "2026-02-01T00:00:00Z"
