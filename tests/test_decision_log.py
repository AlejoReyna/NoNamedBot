"""Tests for strategy decision audit logging."""

from __future__ import annotations

import json

from src.execution.decision_log import DecisionLogger


def test_decision_log_writes_jsonl_record(tmp_path: object) -> None:
    log_path = tmp_path / "decision_log.jsonl"  # type: ignore[operator]
    logger = DecisionLogger(log_path)

    logger.log(
        cycle_number=3,
        mode="paper",
        portfolio_value_usdc=10000.0,
        position_count=1,
        entries_allowed=True,
        action="ENTER",
        reason="4/4 core factors passed (6/6 total)",
        priced_target_count=12,
        symbol="cake",
        position_size_usdc=500.0,
        factor_scores={"slippage_under_cap": True},
        true_factor_count=6,
        estimated_slippage_pct=0.005,
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert set(record) == {
        "timestamp",
        "cycle_number",
        "mode",
        "portfolio_value_usdc",
        "position_count",
        "entries_allowed",
        "action",
        "symbol",
        "position_size_usdc",
        "factor_scores",
        "factor_metrics",
        "true_factor_count",
        "estimated_slippage_pct",
        "reason",
        "priced_target_count",
    }
    assert record["cycle_number"] == 3
    assert record["mode"] == "paper"
    assert record["action"] == "ENTER"
    assert record["symbol"] == "CAKE"
    assert record["factor_scores"] == {"slippage_under_cap": True}
    assert record["factor_metrics"] == {}
    assert record["estimated_slippage_pct"] == 0.005
    assert "timestamp" in record


def test_decision_log_writes_blocked_reason_when_present(tmp_path: object) -> None:
    log_path = tmp_path / "decision_log.jsonl"  # type: ignore[operator]
    logger = DecisionLogger(log_path)

    logger.log(
        cycle_number=4,
        mode="live",
        portfolio_value_usdc=10.0,
        position_count=0,
        entries_allowed=False,
        action="BLOCKED",
        reason="disk guard: free space below threshold",
        priced_target_count=147,
        entries_blocked_reason="disk_guard_free_space_below_threshold",
    )

    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["entries_blocked_reason"] == "disk_guard_free_space_below_threshold"
