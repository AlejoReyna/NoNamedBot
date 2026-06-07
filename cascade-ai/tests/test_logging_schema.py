"""Tests for JSONL logging schemas."""

from __future__ import annotations

import json

from src.common.logging_schema import (
    LiveDecisionLog,
    SentimentLiveLog,
    ShadowDecisionLog,
    append_to_file,
    to_jsonl,
)


def test_live_decision_has_source_live() -> None:
    log = LiveDecisionLog(cycle_id=1, action="WAIT")
    assert log.source == "LIVE"
    assert log.path == "live"


def test_shadow_decision_has_source_shadow() -> None:
    log = ShadowDecisionLog(cycle_id=1, variant="jump")
    assert log.source == "SHADOW"
    assert log.path == "shadow"


def test_cycle_id_correlates_live_and_shadow() -> None:
    live = LiveDecisionLog(cycle_id=42)
    shadow = ShadowDecisionLog(cycle_id=42)
    assert live.cycle_id == shadow.cycle_id


def test_sentiment_log_has_required_fields() -> None:
    log = SentimentLiveLog(
        fear_greed_index=78,
        funding_rate_btc=0.0012,
        gas_price_gwei=0.1,
        sentiment_delta=-1.5,
        regime_fragility="CROWDED_LONG",
    )
    assert log.sentiment_delta == -1.5
    assert log.regime_fragility == "CROWDED_LONG"


def test_to_jsonl_returns_single_parseable_line() -> None:
    payload = to_jsonl(LiveDecisionLog(cycle_id=5, reasons=["ok"]))
    assert "\n" not in payload
    assert json.loads(payload)["cycle_id"] == 5


def test_append_to_file_writes_one_line(tmp_path: object) -> None:
    path = tmp_path / "decision_live.jsonl"  # type: ignore[operator]
    append_to_file(path, LiveDecisionLog(cycle_id=8))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["path"] == "live"
