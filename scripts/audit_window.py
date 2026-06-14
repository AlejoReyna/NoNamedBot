#!/usr/bin/env python3
"""Audit the bot's behavior over a recent time window from the JSONL logs.

Usage:
    python scripts/audit_window.py --hours 10
    python scripts/audit_window.py --hours 10 --logs logs --exec-log execution_log.jsonl
    python scripts/audit_window.py --since 2026-06-14T00:00:00   # explicit start (UTC)

Reads:
    logs/decision_live.jsonl       per-cycle ENTER / WAIT / BLOCKED / HALT
    logs/portfolio_snapshots.jsonl portfolio value, drawdown, open positions
    logs/risk_events.jsonl         kill switch / pause / limit breaches
    execution_log.jsonl            actual swap attempts (tx hashes, errors)

Pure stdlib. Run it locally OR on the EC2 box where the live logs live
(/home/ec2-user/cascade-ai). Nothing here mutates state or touches the wallet.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys


def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def in_window(rows, start):
    out = []
    for r in rows:
        ts = parse_ts(r.get("timestamp"))
        if ts and ts >= start:
            out.append((ts, r))
    return sorted(out, key=lambda x: x[0])


def fmt(ts):
    return ts.strftime("%m-%d %H:%M:%S") if ts else "??"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--since", help="UTC ISO start time; overrides --hours")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--exec-log", default="execution_log.jsonl")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if args.since:
        start = parse_ts(args.since)
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=dt.timezone.utc)
    else:
        start = now - dt.timedelta(hours=args.hours)

    decisions = in_window(load(os.path.join(args.logs, "decision_live.jsonl")), start)
    snaps = in_window(load(os.path.join(args.logs, "portfolio_snapshots.jsonl")), start)
    risks = in_window(load(os.path.join(args.logs, "risk_events.jsonl")), start)
    execs = in_window(load(args.exec_log), start)

    line = "=" * 64
    print(line)
    print(f" CASCADE-AI AUDIT  window start (UTC): {fmt(start)}   now: {fmt(now)}")
    print(line)

    if not any([decisions, snaps, risks, execs]):
        print("\n  No log rows in this window. Either the bot didn't run, or")
        print("  these logs predate the window (check newest timestamps).")
        return

    # --- Decisions ---
    actions = collections.Counter(r.get("action") for _, r in decisions)
    print(f"\nDECISIONS ({len(decisions)} cycles)")
    for act, n in actions.most_common():
        print(f"   {act or '?':<8} {n}")
    modes = collections.Counter(r.get("mode") for _, r in decisions)
    if modes:
        print("   mode:", dict(modes))

    # Block/halt reasons
    reasons = collections.Counter()
    for _, r in decisions:
        if r.get("action") in ("HALT", "BLOCKED"):
            for rs in (r.get("reasons") or []):
                reasons[rs] += 1
    if reasons:
        print("   halt/block reasons:")
        for rs, n in reasons.most_common():
            print(f"      {rs}: {n}")

    # Entries from the decision log
    enters = [(ts, r) for ts, r in decisions if r.get("action") == "ENTER"]
    if enters:
        print(f"\nENTRY SIGNALS ({len(enters)})")
        for ts, r in enters:
            print(f"   {fmt(ts)}  {r.get('symbol')}  size={r.get('size_pct')}  "
                  f"regime={r.get('regime')}  score={r.get('entry_score')}")

    # --- Executions (the real swaps) ---
    if execs:
        print(f"\nEXECUTIONS ({len(execs)})")
        ecnt = collections.Counter(r.get("action") for _, r in execs)
        print("   by action:", dict(ecnt))
        errs = [(ts, r) for ts, r in execs if r.get("error") or
                (isinstance(r.get("result"), dict) and r["result"].get("error"))]
        for ts, r in execs:
            res = r.get("result") or {}
            mode = res.get("mode", "?")
            txt = (f"   {fmt(ts)}  {r.get('action'):<6} "
                   f"{r.get('from_symbol')}->{r.get('to_symbol')}  "
                   f"in={r.get('amount_in')}  mode={mode}")
            print(txt)
        if errs:
            print(f"   !! {len(errs)} execution errors in window")

    # --- Risk events ---
    if risks:
        print(f"\nRISK EVENTS ({len(risks)})")
        for ts, r in risks:
            print(f"   {fmt(ts)}  [{r.get('severity')}] {r.get('event_type')}  "
                  f"{r.get('details')}")

    # --- Portfolio P&L ---
    if snaps:
        first_v = snaps[0][1].get("portfolio_value_usdc")
        last_v = snaps[-1][1].get("portfolio_value_usdc")
        max_dd = max((r.get("drawdown_pct") or 0) for _, r in snaps)
        print("\nPORTFOLIO")
        print(f"   first snapshot {fmt(snaps[0][0])}  value={first_v}")
        print(f"   last  snapshot {fmt(snaps[-1][0])}  value={last_v}")
        if first_v and last_v:
            chg = last_v - first_v
            pct = (chg / first_v * 100) if first_v else 0
            print(f"   change: {chg:+.4f} USDC ({pct:+.2f}%)")
        print(f"   max drawdown in window: {max_dd}%")
        last_pos = snaps[-1][1].get("open_positions") or []
        print(f"   open positions at end: {len(last_pos)}")
        for p in last_pos:
            print(f"      {p.get('symbol')}  entry={p.get('entry_price')}  "
                  f"stop={p.get('trailing_stop_price')}  tp={p.get('take_profit_price')}")

    print("\n" + line)


if __name__ == "__main__":
    main()
