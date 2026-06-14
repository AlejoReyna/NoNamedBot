"""Tests for x402 spend ledger and per-call audit records."""

from __future__ import annotations

import json
from pathlib import Path

from src.data.x402_spend_governor import X402SpendGovernor


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_x402_governor_appends_success_and_failure_call_records(tmp_path: Path) -> None:
    call_log_path = tmp_path / "logs" / "x402_calls.jsonl"
    governor = X402SpendGovernor(
        daily_budget_usdc=2.0,
        total_budget_usdc=15.0,
        cost_per_call_usdc=0.01,
        ledger_path=tmp_path / "x402_spend.json",
        call_log_path=call_log_path,
    )

    governor.record_spend(tool="get_crypto_quotes_latest", http_status=200)
    governor.record_failure(
        tool="get_crypto_market_metrics",
        reason="payment rejected with signature 0x" + "a" * 64 + " " + "b" * 240,
    )

    records = _jsonl(call_log_path)

    assert len(records) == 2
    assert records[0]["outcome"] == "success"
    assert records[0]["tool"] == "get_crypto_quotes_latest"
    assert records[0]["amount_usdc"] == 0.01
    assert records[0]["http_status"] == 200
    assert records[0]["reason"] is None
    assert records[0]["daily_spend_usdc"] == 0.01
    assert records[0]["total_spend_usdc"] == 0.01

    assert records[1]["outcome"] == "failure"
    assert records[1]["tool"] == "get_crypto_market_metrics"
    assert records[1]["amount_usdc"] == 0.01
    assert records[1]["http_status"] is None
    assert records[1]["daily_spend_usdc"] == 0.02
    assert records[1]["total_spend_usdc"] == 0.02
    assert isinstance(records[1]["reason"], str)
    assert len(records[1]["reason"]) <= 200
    assert "0x" + "a" * 64 not in records[1]["reason"]
    assert "b" * 80 not in records[1]["reason"]
    assert isinstance(records[1]["ts"], str)


def test_x402_governor_call_log_write_failure_does_not_raise(tmp_path: Path) -> None:
    governor = X402SpendGovernor(
        daily_budget_usdc=2.0,
        total_budget_usdc=15.0,
        cost_per_call_usdc=0.01,
        ledger_path=tmp_path / "x402_spend.json",
        call_log_path=tmp_path,
    )

    governor.record_spend(tool="get_crypto_quotes_latest")
    governor.record_failure(tool="get_crypto_market_metrics", reason="disk denied")

    ledger = json.loads((tmp_path / "x402_spend.json").read_text(encoding="utf-8"))
    assert ledger["daily_spend_usdc"] == 0.02
    assert ledger["total_spend_usdc"] == 0.02
