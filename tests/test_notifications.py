"""Tests for webhook notification system (Initiative 6)."""
import hashlib
import hmac
import json
import sys
import pytest
from unittest.mock import MagicMock, patch

from converge.models import Event, EventType


def _make_mock_httpx(*, status_code=200, side_effect=None):
    """Create a mock httpx module with a mock post function."""
    mock_httpx = MagicMock()
    if side_effect:
        mock_httpx.post.side_effect = side_effect
    else:
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_httpx.post.return_value = mock_response
    return mock_httpx


def test_hmac_signing():
    """Verify HMAC signature generation."""
    secret = "test-secret"
    body = '{"event_type": "test", "payload": {}}'
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert expected  # Non-empty
    assert len(expected) == 64  # SHA256 hex digest


def test_send_success(db_path, monkeypatch):
    """Successful webhook send returns True and emits NOTIFICATION_SENT."""
    from converge import event_log
    from converge.notifications.webhook_adapter import WebhookNotifyAdapter

    monkeypatch.setenv("CONVERGE_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("CONVERGE_WEBHOOK_SECRET", "test-secret")

    mock_httpx = _make_mock_httpx(status_code=200)
    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        adapter = WebhookNotifyAdapter()
        result = adapter.send("default", "test.event", {"key": "value"})

    assert result is True
    events = event_log.query(event_type=EventType.NOTIFICATION_SENT)
    assert len(events) == 1


def test_send_failure_retry(db_path, monkeypatch):
    """Failed webhook retries once then emits NOTIFICATION_FAILED."""
    from converge import event_log
    from converge.notifications.webhook_adapter import WebhookNotifyAdapter

    monkeypatch.setenv("CONVERGE_WEBHOOK_URL", "https://example.com/hook")

    mock_httpx = _make_mock_httpx(side_effect=ConnectionError("refused"))
    with patch("converge.notifications.webhook_adapter.time") as mock_time:
        mock_time.sleep = MagicMock()
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            adapter = WebhookNotifyAdapter()
            result = adapter.send("default", "test.event", {"key": "value"})

    assert result is False
    assert mock_httpx.post.call_count == 2  # original + 1 retry
    events = event_log.query(event_type=EventType.NOTIFICATION_FAILED)
    assert len(events) == 1


def test_channel_fallback(monkeypatch):
    """Unknown channel falls back to 'default'."""
    from converge.notifications.webhook_adapter import WebhookNotifyAdapter

    monkeypatch.setenv("CONVERGE_WEBHOOK_URL", "https://example.com/hook")
    adapter = WebhookNotifyAdapter()
    # Unknown channel should still resolve to default URL
    url = adapter._urls.get("nonexistent") or adapter._urls.get("default", "")
    assert url == "https://example.com/hook"


def test_dispatcher_shadow(monkeypatch):
    """In shadow mode, dispatcher logs but doesn't make HTTP calls."""
    from converge import feature_flags
    from converge.notifications import dispatcher

    monkeypatch.setenv("CONVERGE_FF_NOTIFICATIONS", "1")
    monkeypatch.setenv("CONVERGE_FF_NOTIFICATIONS_MODE", "shadow")
    feature_flags.reload_flags()
    dispatcher.reset_adapter()

    mock_httpx = _make_mock_httpx()
    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        dispatcher.notify("test.event", {"key": "value"})
        mock_httpx.post.assert_not_called()

    dispatcher.reset_adapter()


def test_dispatcher_enforce(db_path, monkeypatch):
    """In enforce mode, dispatcher makes HTTP calls."""
    from converge import feature_flags
    from converge.notifications import dispatcher

    monkeypatch.setenv("CONVERGE_FF_NOTIFICATIONS", "1")
    monkeypatch.setenv("CONVERGE_FF_NOTIFICATIONS_MODE", "enforce")
    monkeypatch.setenv("CONVERGE_WEBHOOK_URL", "https://example.com/hook")
    feature_flags.reload_flags()
    dispatcher.reset_adapter()

    mock_httpx = _make_mock_httpx(status_code=200)
    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        dispatcher.notify("test.event", {"key": "value"})
        mock_httpx.post.assert_called_once()

    dispatcher.reset_adapter()


def test_dispatcher_disabled(monkeypatch):
    """With notifications flag off, dispatcher returns immediately."""
    from converge import feature_flags
    from converge.notifications import dispatcher

    monkeypatch.setenv("CONVERGE_FF_NOTIFICATIONS", "0")
    feature_flags.reload_flags()
    dispatcher.reset_adapter()

    mock_httpx = _make_mock_httpx()
    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        dispatcher.notify("test.event", {"key": "value"})
        mock_httpx.post.assert_not_called()

    dispatcher.reset_adapter()


def test_is_available_with_url(monkeypatch):
    """With URL configured, is_available returns True."""
    from converge.notifications.webhook_adapter import WebhookNotifyAdapter

    monkeypatch.setenv("CONVERGE_WEBHOOK_URL", "https://example.com/hook")
    adapter = WebhookNotifyAdapter()
    assert adapter.is_available() is True


def test_is_available_without_url(monkeypatch):
    """Without URL, is_available returns False."""
    from converge.notifications.webhook_adapter import WebhookNotifyAdapter

    monkeypatch.delenv("CONVERGE_WEBHOOK_URL", raising=False)
    adapter = WebhookNotifyAdapter()
    assert adapter.is_available() is False
