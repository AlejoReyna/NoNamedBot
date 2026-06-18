#!/usr/bin/env python3
"""CLI for inspecting the verified sell history log.

Usage:
    python scripts/sell_history.py
    python scripts/sell_history.py --symbol AAVE
    python scripts/sell_history.py --since 2026-06-04
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _parse_since(value: str) -> datetime:
    """Parse an ISO date or datetime for the --since filter."""

    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=None)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid date/datetime: {value}") from exc


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _short_hash(tx_hash: str | None) -> str:
    if not tx_hash:
        return "-"
    if tx_hash.startswith("0x") and len(tx_hash) >= 10:
        return tx_hash[:10]
    return tx_hash[:10]


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show verified sells from logs/sell_history.jsonl")
    parser.add_argument(
        "--log-path",
        default=os.getenv("SELL_HISTORY_LOG_PATH", "logs/sell_history.jsonl"),
        help="Path to sell_history.jsonl (default: logs/sell_history.jsonl)",
    )
    parser.add_argument("--symbol", help="Filter by token symbol (case-insensitive)")
    parser.add_argument(
        "--since",
        type=_parse_since,
        help="Only show sells on or after this ISO date/datetime",
    )
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="Also show rows where verified=false",
    )
    args = parser.parse_args(argv)

    path = Path(args.log_path)
    rows = _load_rows(path)

    symbol_filter = (args.symbol or "").upper()
    displayed = 0
    for row in rows:
        if not args.include_unverified and not row.get("verified"):
            continue
        if symbol_filter and row.get("symbol", "").upper() != symbol_filter:
            continue
        ts = _parse_timestamp(row.get("timestamp", ""))
        if ts is None:
            continue
        # Compare naive timestamps; since filter is user-local.
        row_ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
        if args.since is not None and row_ts < args.since:
            continue
        displayed += 1
        print(
            f"{row_ts:%Y-%m-%d %H:%M:%S}  "
            f"{row.get('symbol', '?'):<6}  "
            f"{row.get('amount_sold', 0.0):>12.6f}  "
            f"USDC {row.get('realized_pnl_usdc', 0.0):>10.4f}  "
            f"tx:{_short_hash(row.get('exit_tx_hash'))}  "
            f"{'verified' if row.get('verified') else 'unverified'}"
        )

    if displayed == 0:
        print("No verified sells found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
