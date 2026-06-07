"""Offline A/B comparison for historical decision logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def run_ab_test(
    baseline_log: str,
    variant_logs: list[str],
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Generate directional baseline-vs-variant metrics from JSONL logs."""

    selected = metrics or [
        "trades_per_day",
        "avg_slippage",
        "max_drawdown",
        "win_rate",
        "sentiment_accuracy",
    ]
    baseline = _summarize(_read_jsonl(baseline_log), selected)
    variants = {path: _summarize(_read_jsonl(path), selected) for path in variant_logs}
    return {"baseline": baseline, "variants": variants, "metrics": selected}


def _summarize(records: list[dict[str, Any]], metrics: list[str]) -> dict[str, float]:
    summary: dict[str, float] = {}
    if "trades_per_day" in metrics:
        summary["trades_per_day"] = float(sum(1 for item in records if _action(item) == "ENTER"))
    if "avg_slippage" in metrics:
        slippages = [_number(item.get("estimated_slippage_pct", item.get("slippage_quote"))) for item in records]
        values = [value for value in slippages if value is not None]
        summary["avg_slippage"] = sum(values) / len(values) if values else 0.0
    if "max_drawdown" in metrics:
        drawdowns = [_number(item.get("drawdown_pct")) for item in records]
        summary["max_drawdown"] = max([value for value in drawdowns if value is not None] or [0.0])
    if "win_rate" in metrics:
        wins = [item for item in records if _number(item.get("realized_pnl_pct")) is not None]
        summary["win_rate"] = _win_rate(wins)
    if "sentiment_accuracy" in metrics:
        summary["sentiment_accuracy"] = _sentiment_accuracy(records)
    return summary


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _action(record: dict[str, Any]) -> str:
    return str(record.get("action", record.get("hypothetical_action", ""))).upper()


def _win_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    wins = sum(1 for item in records if (_number(item.get("realized_pnl_pct")) or 0.0) > 0)
    return wins / len(records)


def _sentiment_accuracy(records: list[dict[str, Any]]) -> float:
    checked = 0
    correct = 0
    for item in records:
        delta = _number(item.get("sentiment_delta"))
        next_drawdown = _number(item.get("next_cycle_drawdown_pct"))
        if delta is None or next_drawdown is None:
            continue
        checked += 1
        if (delta < 0 and next_drawdown <= 0) or (delta >= 0 and next_drawdown >= 0):
            correct += 1
    return correct / checked if checked else 0.0
