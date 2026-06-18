"""Verified sell history log.

Each row represents an exit whose on-chain balance change has been confirmed.
The log is append-only and best-effort: failures are swallowed so the trading
cycle is never blocked by logging.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PATH = "logs/sell_history.jsonl"


def record_verified_exit(
    path_str: str,
    *,
    timestamp: str | None = None,
    symbol: str,
    trade_id: str | None,
    exit_price: float | None,
    amount_sold: float,
    expected_amount_out: float | None,
    balance_before: float | None,
    balance_after: float | None,
    exit_tx_hash: str | None,
    exit_reason: str | None,
    realized_pnl_usdc: float,
    verified: bool,
) -> None:
    """Append one verified (or failed-verification) sell row.

    Swallows all write errors so logging never blocks the trading cycle.
    """

    try:
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "symbol": str(symbol).upper(),
            "trade_id": trade_id,
            "exit_price": exit_price,
            "amount_sold": amount_sold,
            "expected_amount_out": expected_amount_out,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "exit_tx_hash": exit_tx_hash,
            "exit_reason": exit_reason,
            "realized_pnl_usdc": realized_pnl_usdc,
            "verified": bool(verified),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:  # logging must never block an exit
        LOGGER.debug("Could not append verified sell history: %s", exc)
