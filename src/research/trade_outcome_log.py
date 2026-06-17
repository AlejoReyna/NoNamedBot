"""Append-only entry/exit outcome log for factor-combination backtesting.

Each trade writes two JSONL events to the same file: an ``entry`` event that
captures the factor scores the engine entered on, and a later ``exit`` event
that captures realized PnL. The two share a ``trade_id`` so re-entries on the
same symbol stay distinct (a plain symbol+time join goes fuzzy when a symbol is
traded more than once), plus ``opened_at`` / ``closed_at`` and entry/exit tx
hashes for an even stronger join against on-chain records.

Only one position per symbol is open at a time (the caller checks for an open
position before entering), so the live ``trade_id`` is persisted on the
Position. A simple symbol -> id map is kept as an in-process fallback for
callers that do not pass an explicit id; after a restart, persisted positions
still preserve the join while legacy/reconstructed rows fall back to
symbol/time.

Both writers are best-effort: any failure is swallowed so logging can never
take down a live trading cycle.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PATH = "logs/trade_outcomes.jsonl"

# symbol -> trade_id for the currently-open position on that symbol.
_OPEN_TRADES: dict[str, str] = {}


def new_trade_id() -> str:
    """Generate a fresh trade id. Persist it on the Position so the entry/exit
    join survives a process restart (the in-memory map does not)."""

    return uuid.uuid4().hex


def _append(path_str: str, payload: dict[str, Any]) -> None:
    try:
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        LOGGER.debug("Could not append trade outcome event: %s", exc)


def record_entry(
    path_str: str,
    *,
    symbol: str,
    entry_price: float,
    size_usdc: float,
    entry_score: float | None,
    true_factor_count: int | None,
    factor_scores: dict[str, bool] | None,
    estimated_slippage_pct: float | None = None,
    entry_tx_hash: str | None = None,
    trade_id: str | None = None,
    # Market context logged for richer ML features (all optional / backward-compat)
    atr_pct: float | None = None,
    regime: str | None = None,
    bnb_1h_pct: float | None = None,
    bnb_24h_pct: float | None = None,
) -> str:
    """Record the factors an entry was taken on. Returns the ``trade_id``.

    Pass ``trade_id`` (e.g. one stamped onto the persisted Position) to keep the
    entry/exit join stable across restarts; otherwise a fresh id is generated.

    The optional market-context params (atr_pct, regime, bnb_*) are logged
    alongside the factor scores so the ML dataset has richer entry-time signal
    without any leakage risk — all values are available before the swap executes.
    """

    symbol_key = str(symbol).upper()
    opened_at = time.time()
    trade_id = trade_id or new_trade_id()
    _OPEN_TRADES[symbol_key] = trade_id

    _append(
        path_str,
        {
            "event": "entry",
            "trade_id": trade_id,
            "ts": opened_at,
            "opened_at": opened_at,
            "symbol": symbol_key,
            "entry_price": entry_price,
            "size_usdc": size_usdc,
            "entry_score": entry_score,
            "true_factor_count": true_factor_count,
            "factor_scores": factor_scores or {},
            "estimated_slippage_pct": estimated_slippage_pct,
            "entry_tx_hash": entry_tx_hash,
            "atr_pct": atr_pct,
            "regime": regime,
            "bnb_1h_pct": bnb_1h_pct,
            "bnb_24h_pct": bnb_24h_pct,
        },
    )
    return trade_id


def record_exit(
    path_str: str,
    *,
    symbol: str,
    entry_price: float | None,
    exit_price: float | None,
    realized_pnl_usdc: float,
    exit_reason: str | None = None,
    hold_time_seconds: float | None = None,
    exit_tx_hash: str | None = None,
    trade_id: str | None = None,
) -> None:
    """Record the realized outcome of an exit, joined to the entry by trade_id.

    Prefers an explicit ``trade_id`` (e.g. read from the persisted Position) so
    the join holds even after a restart that cleared the in-memory map; falls
    back to the map otherwise.
    """

    symbol_key = str(symbol).upper()
    closed_at = time.time()
    mapped_trade_id = _OPEN_TRADES.pop(symbol_key, None)
    trade_id = trade_id or mapped_trade_id

    realized_pnl_pct: float | None = None
    if entry_price and exit_price and entry_price > 0:
        realized_pnl_pct = (exit_price - entry_price) / entry_price

    _append(
        path_str,
        {
            "event": "exit",
            "trade_id": trade_id,
            "ts": closed_at,
            "closed_at": closed_at,
            "symbol": symbol_key,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "realized_pnl_usdc": realized_pnl_usdc,
            "realized_pnl_pct": realized_pnl_pct,
            "exit_reason": exit_reason,
            "hold_time_seconds": hold_time_seconds,
            "exit_tx_hash": exit_tx_hash,
        },
    )
