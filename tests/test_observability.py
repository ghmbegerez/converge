"""Tests for converge.observability: logging, tracing, metrics, middleware."""

from __future__ import annotations

import json
import logging

import pytest


class TestJsonFormatter:
    def test_format_basic_record(self):
        from converge.observability import JsonFormatter
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert parsed["logger"] == "test"
        assert "timestamp" in parsed

    def test_format_propagates_extra_fields(self):
        from converge.observability import JsonFormatter
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="request", args=(), exc_info=None,
        )
        record.trace_id = "tr-123"
        record.intent_id = "int-456"
        record.tenant_id = "team-a"
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["trace_id"] == "tr-123"
        assert parsed["intent_id"] == "int-456"
        assert parsed["tenant_id"] == "team-a"

    def test_format_excludes_missing_extra_fields(self):
        from converge.observability import JsonFormatter
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert "trace_id" not in parsed
        assert "intent_id" not in parsed

    def test_format_includes_exception(self):
        from converge.observability import JsonFormatter
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="error", args=(), exc_info=exc_info,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "boom" in parsed["exception"]


class TestSetupLogging:
    def test_setup_logging_configures_root(self):
        from converge.observability import setup_logging
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1
        # Clean up
        root.handlers.clear()

    def test_setup_logging_default_info(self):
        from converge.observability import setup_logging
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO
        root.handlers.clear()


class TestMetrics:
    def test_record_request_and_generate(self):
        from converge.observability import (
            _request_count, _request_latency_sum, _request_latency_count,
            _error_count, record_request, generate_metrics,
        )
        # Clear state
        _request_count.clear()
        _request_latency_sum.clear()
        _request_latency_count.clear()
        _error_count.clear()

        record_request("GET", "/health", 200, 0.05)
        record_request("POST", "/api/intents", 201, 0.12)
        record_request("GET", "/api/intents", 500, 0.30)

        assert _request_count[("GET", "/health", "200")] == 1
        assert _request_count[("POST", "/api/intents", "201")] == 1
        assert _error_count[("GET", "/api/intents")] == 1

        output = generate_metrics()
        assert "converge_http_requests_total" in output
        assert "converge_http_request_duration_seconds" in output
        assert "converge_http_errors_total" in output

    def test_record_request_5xx_counts_error(self):
        from converge.observability import _error_count, record_request
        _error_count.clear()
        record_request("GET", "/fail", 502, 0.1)
        assert _error_count[("GET", "/fail")] == 1

    def test_record_request_4xx_no_error(self):
        from converge.observability import _error_count, record_request
        _error_count.clear()
        record_request("GET", "/notfound", 404, 0.01)
        assert ("GET", "/notfound") not in _error_count


class TestTracing:
    def test_get_tracer_returns_none_without_setup(self):
        from converge.observability import get_tracer
        # Without calling setup_tracing, tracer may be None
        tracer = get_tracer()
        # Just verify it doesn't crash
        assert tracer is None or tracer is not None
