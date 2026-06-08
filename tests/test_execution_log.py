"""Tests for execution audit logging."""

from __future__ import annotations

import json

from src.execution.execution_log import ExecutionLogger


def test_execution_log_writes_jsonl_record(tmp_path: object) -> None:
    log_path = tmp_path / "execution_log.jsonl"  # type: ignore[operator]
    logger = ExecutionLogger(log_path)

    logger.log(
        action="entry",
        from_symbol="usdc",
        to_symbol="cake",
        amount_in=100.0,
        max_slippage_pct=0.01,
        expected_amount_out=50.0,
        result={
            "tx_hash": "0x" + "1" * 64,
            "approval_hash": "0x" + "2" * 64,
            "mode": "twak",
        },
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "entry"
    assert record["from_symbol"] == "USDC"
    assert record["to_symbol"] == "CAKE"
    assert record["amount_in"] == 100.0
    assert record["expected_amount_out"] == 50.0
    assert record["tx_hash"] == "0x" + "1" * 64
    assert record["approval_hash"] == "0x" + "2" * 64
    assert record["result"]["mode"] == "twak"
    assert "timestamp" in record
