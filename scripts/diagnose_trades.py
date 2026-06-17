#!/usr/bin/env python3
"""Summarize realized trade performance and guardrail activity.

Pure stdlib — runs with any Python on the box, no pandas/ML deps.

Answers three questions:
  1. Entry quality   — win rate, PnL per trade, by symbol, by factor count.
  2. Risk activity   — how often each guardrail/reason fires in the decision log.
  3. The loop        — does entry quality plausibly explain the drawdown halts?

Usage:
    python scripts/diagnose_trades.py
    python scripts/diagnose_trades.py --trades logs/trade_outcomes.jsonl \
        --decisions logs/decision_live.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _num(v: object) -> float | None:
    try:
        if v in (None, "", "-"):
            return None
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _join_trades(events: list[dict]) -> list[dict]:
    """Join entry+exit events by trade_id into one record per closed trade."""
    entries: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    for e in events:
        tid = e.get("trade_id")
        if not tid:
            continue
        if e.get("event") == "entry":
            entries[tid] = e
        elif e.get("event") == "exit":
            exits[tid] = e
    trades = []
    for tid, entry in entries.items():
        ex = exits.get(tid)
        if ex is None:
            continue  # still open
        trades.append({**entry, **{f"exit_{k}": v for k, v in ex.items()}, "trade_id": tid})
    return trades


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "n/a"


def report_trades(trades: list[dict]) -> None:
    print(f"\n== ENTRY QUALITY :: {len(trades)} closed trades ==")
    if not trades:
        print("  No closed trades yet — nothing to evaluate.")
        return

    pnls = [(_num(t.get("exit_realized_pnl_usdc")) or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    print(f"  win rate:        {_pct(len(wins), len(trades))}  ({len(wins)}W / {len(losses)}L)")
    print(f"  total PnL:       {total:+.4f} USDC")
    print(f"  avg / trade:     {total / len(trades):+.4f} USDC")
    if wins:
        print(f"  avg win:         {sum(wins) / len(wins):+.4f}   best: {max(wins):+.4f}")
    if losses:
        print(f"  avg loss:        {sum(losses) / len(losses):+.4f}   worst: {min(losses):+.4f}")
    if wins and losses and sum(losses) != 0:
        print(f"  profit factor:   {abs(sum(wins) / sum(losses)):.2f}  (>1 = profitable)")

    # By number of true factors at entry — does conviction predict outcome?
    by_fc: dict[int, list[float]] = defaultdict(list)
    for t, p in zip(trades, pnls):
        fc = t.get("true_factor_count")
        if fc is None:
            fs = t.get("factor_scores") or {}
            fc = sum(1 for v in fs.values() if v)
        by_fc[int(fc)].append(p)
    print("\n  outcome by true-factor-count at entry:")
    for fc in sorted(by_fc):
        ps = by_fc[fc]
        w = sum(1 for p in ps if p > 0)
        print(f"    {fc} factors: {len(ps):3d} trades  win {_pct(w, len(ps)):>4}  totalPnL {sum(ps):+.4f}")

    # By exit reason — which exits are bleeding?
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t, p in zip(trades, pnls):
        by_reason[str(t.get("exit_exit_reason") or "?")].append(p)
    print("\n  outcome by exit reason:")
    for reason, ps in sorted(by_reason.items(), key=lambda kv: sum(kv[1])):
        w = sum(1 for p in ps if p > 0)
        print(f"    {reason:18s} {len(ps):3d} trades  win {_pct(w, len(ps)):>4}  totalPnL {sum(ps):+.4f}")

    # Worst symbols
    by_sym: dict[str, float] = defaultdict(float)
    for t, p in zip(trades, pnls):
        by_sym[str(t.get("symbol") or "?")] += p
    worst = sorted(by_sym.items(), key=lambda kv: kv[1])[:5]
    print("\n  worst symbols by PnL:", ", ".join(f"{s} {v:+.3f}" for s, v in worst))


def report_guardrails(decisions: list[dict]) -> None:
    print(f"\n== RISK ACTIVITY :: {len(decisions)} decisions ==")
    if not decisions:
        print("  No decision log found.")
        return
    actions = Counter(d.get("action") for d in decisions)
    print("  actions:", dict(actions))
    rc: Counter = Counter()
    for d in decisions:
        rs = d.get("reasons")
        if isinstance(rs, list):
            for x in rs:
                rc[str(x)[:50]] += 1
        elif d.get("reason"):
            rc[str(d["reason"])[:50]] += 1
    print("  reason frequency:")
    for reason, c in rc.most_common(12):
        print(f"    {c:4d}  {_pct(c, len(decisions)):>4}  {reason}")
    halts = sum(1 for d in decisions if d.get("action") == "HALT")
    print(f"\n  cycles fully halted: {halts}/{len(decisions)} ({_pct(halts, len(decisions))})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trades", default="logs/trade_outcomes.jsonl")
    p.add_argument("--decisions", default="logs/decision_live.jsonl")
    args = p.parse_args()

    trades = _join_trades(_load_jsonl(Path(args.trades)))
    report_trades(trades)
    report_guardrails(_load_jsonl(Path(args.decisions)))

    print("\n== READ THIS ==")
    print("  If win rate is low / profit factor < 1 → entry quality is the problem")
    print("  (the ML entry filter targets exactly this). If trades are fine but")
    print("  drawdown halts dominate → the guardrail thresholds are too tight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
