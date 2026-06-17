"""Build leakage-audited offline datasets from trade outcomes and CMC features."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from src.research.feature_contract import (
    REPRODUCIBLE_SCALAR_FEATURES,
    entry_feature_vector,
    is_model_feature,
)

__all__ = [
    "build_dataset",
    "feature_columns",
    "assert_no_leakage",
    "entry_feature_vector",
    "load_trade_outcomes",
    "load_cmc_quotes",
]

DEFAULT_TRADE_OUTCOMES_PATH = "logs/trade_outcomes.jsonl"
DEFAULT_CMC_DB_PATH = "data/cmc_premium.db"

EXIT_ONLY_COLUMNS = {
    "closed_at",
    "exit_price",
    "exit_reason",
    "exit_tx_hash",
    "hold_time_seconds",
    "realized_pnl_pct",
    "realized_pnl_usdc",
}
IDENTIFIER_COLUMNS = {
    "event",
    "trade_id",
    "entry_tx_hash",
    "symbol",
}
LABEL_COLUMNS = {
    "entry_win",
}


def load_trade_outcomes(path: str | Path = DEFAULT_TRADE_OUTCOMES_PATH) -> pd.DataFrame:
    """Load append-only trade outcome JSONL into a normalized DataFrame."""

    source = Path(path)
    if not source.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return pd.DataFrame(rows)


def _flatten_factor_scores(entries: pd.DataFrame) -> pd.DataFrame:
    if entries.empty or "factor_scores" not in entries.columns:
        return entries
    factor_rows: list[dict[str, int]] = []
    for raw in entries["factor_scores"]:
        factors = raw if isinstance(raw, dict) else {}
        factor_rows.append({f"factor_{key}": int(bool(value)) for key, value in factors.items()})
    factor_frame = pd.DataFrame(factor_rows, index=entries.index).fillna(0).astype(int)
    return pd.concat([entries.drop(columns=["factor_scores"]), factor_frame], axis=1)


def _join_entries_and_exits(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "event" not in events.columns:
        return pd.DataFrame()

    entries = events[events["event"] == "entry"].copy()
    exits = events[events["event"] == "exit"].copy()
    if entries.empty or exits.empty:
        return pd.DataFrame()

    entries["symbol"] = entries["symbol"].astype(str).str.upper()
    exits["symbol"] = exits["symbol"].astype(str).str.upper()
    entries["opened_at"] = pd.to_numeric(entries.get("opened_at", entries.get("ts")), errors="coerce")
    exits["closed_at"] = pd.to_numeric(exits.get("closed_at", exits.get("ts")), errors="coerce")
    entries = entries.drop(columns=[col for col in EXIT_ONLY_COLUMNS if col in entries.columns], errors="ignore")

    joined = _join_by_trade_id(entries, exits)
    unmatched_entries = entries
    if not joined.empty and "trade_id" in joined.columns:
        matched_ids = set(joined["trade_id"].dropna().astype(str))
        unmatched_entries = entries[~entries["trade_id"].astype(str).isin(matched_ids)]
    fallback = _join_by_symbol_time(unmatched_entries, exits)
    if fallback.empty:
        return joined.reset_index(drop=True)
    if joined.empty:
        return fallback.reset_index(drop=True)
    return pd.concat([joined, fallback], ignore_index=True, sort=False)


def _join_by_trade_id(entries: pd.DataFrame, exits: pd.DataFrame) -> pd.DataFrame:
    if "trade_id" not in entries.columns or "trade_id" not in exits.columns:
        return pd.DataFrame()
    entry_keyed = entries[entries["trade_id"].notna()].copy()
    exit_keyed = exits[exits["trade_id"].notna()].copy()
    if entry_keyed.empty or exit_keyed.empty:
        return pd.DataFrame()
    exit_cols = [
        col
        for col in ["trade_id", "closed_at", "exit_price", "realized_pnl_usdc", "realized_pnl_pct", "exit_reason", "hold_time_seconds"]
        if col in exit_keyed.columns
    ]
    exit_latest = exit_keyed.sort_values("closed_at").drop_duplicates("trade_id", keep="last")
    return entry_keyed.merge(exit_latest[exit_cols], on="trade_id", how="inner")


def _join_by_symbol_time(entries: pd.DataFrame, exits: pd.DataFrame) -> pd.DataFrame:
    if entries.empty or exits.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    exits_sorted = exits.sort_values("closed_at")
    used_exit_indexes: set[int] = set()
    for _, entry in entries.sort_values("opened_at").iterrows():
        candidates = exits_sorted[
            (exits_sorted["symbol"] == entry["symbol"])
            & (exits_sorted["closed_at"] >= entry["opened_at"])
            & (~exits_sorted.index.isin(used_exit_indexes))
        ]
        if candidates.empty:
            continue
        exit_row = candidates.iloc[0]
        used_exit_indexes.add(int(exit_row.name))
        combined = entry.to_dict()
        for col in ["closed_at", "exit_price", "realized_pnl_usdc", "realized_pnl_pct", "exit_reason", "hold_time_seconds"]:
            if col in exit_row:
                combined[col] = exit_row[col]
        rows.append(combined)
    return pd.DataFrame(rows)


def load_cmc_quotes(db_path: str | Path = DEFAULT_CMC_DB_PATH) -> pd.DataFrame:
    """Load CMC quote rows from SQLite; returns an empty frame when absent."""

    source = Path(db_path)
    if not source.exists() or source.stat().st_size == 0:
        return pd.DataFrame(columns=["symbol", "quote_timestamp"])
    with sqlite3.connect(source) as conn:
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", conn)
        if "quotes" not in set(tables["name"]):
            return pd.DataFrame(columns=["symbol", "quote_timestamp"])
        quotes = pd.read_sql_query("SELECT * FROM quotes", conn)
    if quotes.empty:
        return pd.DataFrame(columns=["symbol", "quote_timestamp"])
    quotes["symbol"] = quotes["symbol"].astype(str).str.upper()
    quotes["quote_timestamp"] = pd.to_datetime(quotes["timestamp"], utc=True, errors="coerce")
    return quotes.drop(columns=["timestamp"]).sort_values(["symbol", "quote_timestamp"]).reset_index(drop=True)


def _merge_asof_cmc(trades: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or quotes.empty:
        return trades
    trades = trades.copy()
    trades["opened_dt"] = pd.to_datetime(trades["opened_at"], unit="s", utc=True, errors="coerce")
    merged_parts: list[pd.DataFrame] = []
    for symbol, group in trades.sort_values("opened_dt").groupby("symbol", sort=False):
        quote_group = quotes[quotes["symbol"] == symbol].sort_values("quote_timestamp")
        if quote_group.empty:
            merged_parts.append(group)
            continue
        quote_group = quote_group.drop(columns=["symbol"])
        merged = pd.merge_asof(
            group.sort_values("opened_dt"),
            quote_group,
            left_on="opened_dt",
            right_on="quote_timestamp",
            direction="backward",
        )
        merged_parts.append(merged)
    return pd.concat(merged_parts, ignore_index=True, sort=False)


def build_dataset(
    trade_outcomes_path: str | Path = DEFAULT_TRADE_OUTCOMES_PATH,
    cmc_db_path: str | Path = DEFAULT_CMC_DB_PATH,
) -> pd.DataFrame:
    """Return one labeled row per closed trade using only entry-time features."""

    events = load_trade_outcomes(trade_outcomes_path)
    trades = _join_entries_and_exits(events)
    if trades.empty:
        return pd.DataFrame()

    trades = _flatten_factor_scores(trades)
    quotes = load_cmc_quotes(cmc_db_path)
    frame = _merge_asof_cmc(trades, quotes)
    # Coerce reproducible scalars to numeric so they survive the feature filter
    # (entry_score arrives as None on some rows, which yields object dtype).
    for scalar in REPRODUCIBLE_SCALAR_FEATURES:
        if scalar in frame.columns:
            frame[scalar] = pd.to_numeric(frame[scalar], errors="coerce").fillna(0.0)
        else:
            frame[scalar] = 0.0
    frame["entry_win"] = pd.to_numeric(frame["realized_pnl_usdc"], errors="coerce").fillna(0.0) > 0.0
    frame["entry_win"] = frame["entry_win"].astype(int)
    return frame.sort_values("opened_at").reset_index(drop=True)


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return the reproducible, entry-time-safe model feature columns.

    Whitelist (not blacklist): only flattened ``factor_*`` indicators and the
    reproducible scalars qualify. This excludes timestamps (``opened_at``),
    identifiers, prices/sizes, CMC quote columns, and all exit fields — every
    one of which is either unavailable or skewed at serving time.
    """

    candidates: list[str] = []
    for column in frame.columns:
        if not is_model_feature(str(column)):
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            candidates.append(str(column))
    return candidates


def assert_no_leakage(columns: list[str] | pd.Index) -> bool:
    """Raise if the feature set contains anything outside the reproducible whitelist.

    Catches not just exit/outcome fields but also timestamps, identifiers,
    prices, sizes, and CMC quote columns — anything that would leak the future
    or that the live shadow path cannot reproduce without train/serve skew.
    """

    names = [str(col) for col in columns]
    leaked_exit = sorted((set(names) & EXIT_ONLY_COLUMNS) - LABEL_COLUMNS)
    if leaked_exit:
        raise ValueError(f"exit-only fields cannot be model features: {', '.join(leaked_exit)}")
    non_reproducible = sorted(name for name in names if not is_model_feature(name))
    if non_reproducible:
        raise ValueError(
            "non-reproducible / non-entry-time fields cannot be model features: "
            + ", ".join(non_reproducible)
        )
    return True
