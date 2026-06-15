"""Tests for health check HTTP server."""

from __future__ import annotations

import json
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
