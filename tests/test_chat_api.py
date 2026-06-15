"""Tests for market chat API."""

from __future__ import annotations

from src.deployment.chat_api import build_chat_reply


def test_chat_help_reply() -> None:
    out = build_chat_reply("hello")
    assert "reply" in out
    assert "questions" in out["reply"].lower()


def test_chat_health_reply() -> None:
    out = build_chat_reply(
        "bot status",
        health_snapshot={"status": "ok", "positions": 2, "daily_trades": 3},
    )
    assert "Open positions: 2" in out["reply"]
    assert out["source"] == "health snapshot"
