"""Tests for health check HTTP server."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from src.deployment.health_state import HealthState
from src.deployment.health_server import start_health_server


def test_health_endpoint_returns_required_keys() -> None:
    state = HealthState()
    state.update(status="ok", positions=2, daily_trades=1, drawdown_pct=4.2)
    server = start_health_server(state, host="127.0.0.1", port=18080, decision_log_path="decision_log.jsonl")
    try:
        with urllib.request.urlopen("http://127.0.0.1:18080/health", timeout=2) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        for key in ("status", "positions", "daily_trades", "drawdown_pct"):
            assert key in payload
        assert payload["positions"] == 2
    finally:
        server.shutdown()


def test_chat_ui_served_at_root() -> None:
    state = HealthState()
    server = start_health_server(state, host="127.0.0.1", port=18081, chat_path="static/chat.html")
    try:
        with urllib.request.urlopen("http://127.0.0.1:18081/", timeout=2) as resp:
            html = resp.read().decode("utf-8")
        assert "Market Terminal" in html
        assert "nav-terminal" in html
        assert "terminal-btn" in html
    finally:
        server.shutdown()


def test_auth_rejects_missing_token(monkeypatch: object) -> None:
    """Returns 401 when HEALTH_API_TOKEN is set but no Authorization header is sent."""
    monkeypatch.setenv("HEALTH_API_TOKEN", "secret-test-token")  # type: ignore[attr-defined]
    state = HealthState()
    state.update(status="ok")
    server = start_health_server(state, host="127.0.0.1", port=18082, decision_log_path="decision_log.jsonl")
    try:
        try:
            urllib.request.urlopen("http://127.0.0.1:18082/health", timeout=2)
            raise AssertionError("Expected 401 but request succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        server.shutdown()
        monkeypatch.delenv("HEALTH_API_TOKEN", raising=False)  # type: ignore[attr-defined]


def test_auth_accepts_correct_bearer_token(monkeypatch: object) -> None:
    """Returns 200 when the correct Bearer token is sent."""
    monkeypatch.setenv("HEALTH_API_TOKEN", "secret-test-token")  # type: ignore[attr-defined]
    state = HealthState()
    state.update(status="ok", positions=0, daily_trades=0, drawdown_pct=0.0)
    server = start_health_server(state, host="127.0.0.1", port=18083, decision_log_path="decision_log.jsonl")
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:18083/health",
            headers={"Authorization": "Bearer secret-test-token"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["status"] == "ok"
    finally:
        server.shutdown()
        monkeypatch.delenv("HEALTH_API_TOKEN", raising=False)  # type: ignore[attr-defined]


def test_response_includes_security_headers() -> None:
    """All responses must include X-Frame-Options and X-Content-Type-Options."""
    state = HealthState()
    state.update(status="ok", positions=0, daily_trades=0, drawdown_pct=0.0)
    server = start_health_server(state, host="127.0.0.1", port=18084, decision_log_path="decision_log.jsonl")
    try:
        with urllib.request.urlopen("http://127.0.0.1:18084/health", timeout=2) as resp:
            assert resp.headers.get("X-Frame-Options") == "DENY"
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    finally:
        server.shutdown()
