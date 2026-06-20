#!/usr/bin/env python3
"""Estimate the return on x402 paid-enrichment spend.

Reads the x402 call log, decision log, and verified sell history, then
attributes paid data costs to the trades they supported and reports:

  - total x402 spend
  - number of trades taken while paid enrichment was active
  - realized PnL and win rate
  - ROI of the data spend (net profit / data cost)

Usage:
    python -m scripts.analyze_x402_roi.py
    python -m scripts.analyze_x402_roi.py --decision-log logs/decision_live.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_X402_LOG = "logs/x402_calls.jsonl"
DEFAULT_DECISION_LOG = "logs/decision_live.jsonl"
DEFAULT_SELL_HISTORY = "logs/sell_history.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Accept ISO strings with or without trailing Z.
        text = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _sum_successful_spend(calls: list[dict[str, Any]]) -> float:
    return sum(
        float(c.get("amount_usdc", 0.0) or 0.0)
        for c in calls
        if c.get("outcome") == "success"
    )


def _match_costs_to_trades(
    entries: list[dict[str, Any]], calls: list[dict[str, Any]]
) -> dict[str, float]:
    """Attribute x402 spend to the nearest ENTER decision within 60 seconds."""
    entry_times: list[tuple[datetime, str]] = []
    for record in entries:
        if record.get("action") != "ENTER":
            continue
        ts = _parse_ts(record.get("timestamp"))
        if ts is None:
            continue
        symbol = str(record.get("symbol") or "UNKNOWN").upper()
        entry_times.append((ts, symbol))

    costs: dict[str, float] = defaultdict(float)
    for call in calls:
        if call.get("outcome") != "success":
            continue
        ts = _parse_ts(call.get("ts"))
        if ts is None:
            continue
        amount = float(call.get("amount_usdc", 0.0) or 0.0)
        if amount <= 0:
            continue
        # Attribute to the nearest entry within 60 s; otherwise bucket as
        # "unattributed" (regime/reference enrichment, etc.).
        best_symbol = "UNATTRIBUTED"
        best_delta = 60.0
        for entry_ts, symbol in entry_times:
            delta = abs((ts - entry_ts).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_symbol = symbol
        costs[best_symbol] += amount
    return dict(costs)


def _analyze(
    x402_log: Path,
    decision_log: Path,
    sell_history: Path,
) -> dict[str, Any]:
    calls = _read_jsonl(x402_log)
    decisions = _read_jsonl(decision_log)
    sells = _read_jsonl(sell_history)

    total_spend = _sum_successful_spend(calls)
    entry_count = sum(1 for d in decisions if d.get("action") == "ENTER")
    cost_by_symbol = _match_costs_to_trades(
        [d for d in decisions if d.get("action") == "ENTER"], calls
    )

    realized_pnl = 0.0
    winning_trades = 0
    losing_trades = 0
    for sell in sells:
        pnl = float(sell.get("realized_pnl_usdc", 0.0) or 0.0)
        realized_pnl += pnl
        if pnl > 0:
            winning_trades += 1
        elif pnl < 0:
            losing_trades += 1

    closed_trades = winning_trades + losing_trades
    win_rate = winning_trades / closed_trades if closed_trades > 0 else 0.0
    net = realized_pnl - total_spend
    roi = net / total_spend if total_spend > 0 else 0.0

    return {
        "total_x402_spend_usdc": round(total_spend, 4),
        "successful_x402_calls": sum(1 for c in calls if c.get("outcome") == "success"),
        "failed_x402_calls": sum(1 for c in calls if c.get("outcome") == "failure"),
        "entry_decisions": entry_count,
        "closed_trades": closed_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 4),
        "realized_pnl_usdc": round(realized_pnl, 4),
        "net_after_data_cost_usdc": round(net, 4),
        "roi_on_data_spend": round(roi, 4),
        "cost_attribution": {
            symbol: round(cost, 4) for symbol, cost in sorted(cost_by_symbol.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze x402 enrichment ROI")
    parser.add_argument(
        "--x402-log",
        type=Path,
        default=Path(DEFAULT_X402_LOG),
        help="Path to x402 call log",
    )
    parser.add_argument(
        "--decision-log",
        type=Path,
        default=Path(DEFAULT_DECISION_LOG),
        help="Path to decision log",
    )
    parser.add_argument(
        "--sell-history",
        type=Path,
        default=Path(DEFAULT_SELL_HISTORY),
        help="Path to verified sell history",
    )
    args = parser.parse_args()

    result = _analyze(args.x402_log, args.decision_log, args.sell_history)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
