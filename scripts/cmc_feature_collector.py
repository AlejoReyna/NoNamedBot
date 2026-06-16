#!/usr/bin/env python3
"""Background CMC premium feature collector (parallel ML experiment)."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import load_settings
from src.config.tokens import TRADABLE_TARGET_SYMBOLS
from src.execution.twak_interface import TWAKInterface

DB_PATH = Path("data/cmc_premium.db")
RAW_DIR = Path("data/cmc_premium")
CACHE_PATH = Path("logs/market_snapshot_cache.json")
PRICE_CACHE_PATH = Path("price_cache.json")
VOLUME_CACHE_PATH = Path("volume_cache.json")


def _update_local_cache(path: Path, updates: dict[str, float], max_age_hours: float = 24.0) -> None:
    """Merge new {symbol: value} points into a LocalCache-format JSON file.

    Format on disk: {"BTC": [{"timestamp": <unix>, "value": <float>}, ...], ...}
    Matches what BreakoutEngine.LocalCache writes, so the dashboard and
    breakout engine both read the same file.
    """
    try:
        existing: dict[str, list[dict]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        existing = {}

    now = time.time()
    cutoff = now - max_age_hours * 3600
    for symbol, value in updates.items():
        if not isinstance(value, (int, float)) or value != value:  # skip NaN
            continue
        points = [pt for pt in existing.get(symbol, []) if pt.get("timestamp", 0) >= cutoff]
        points.append({"timestamp": now, "value": float(value)})
        existing[symbol] = points

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing), encoding="utf-8")
    tmp.replace(path)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS funding_rates (
            token TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            funding_rate REAL,
            open_interest REAL,
            PRIMARY KEY (token, timestamp)
        );
        CREATE TABLE IF NOT EXISTS fear_greed (
            timestamp TEXT PRIMARY KEY,
            value REAL,
            classification TEXT
        );
        CREATE TABLE IF NOT EXISTS market_metrics (
            timestamp TEXT PRIMARY KEY,
            btc_dominance REAL,
            altcoin_volume REAL,
            payload_json TEXT
        );
        CREATE TABLE IF NOT EXISTS quotes (
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            price REAL,
            percent_change_1h REAL,
            percent_change_24h REAL,
            volume_24h REAL,
            volume_1h REAL,
            market_cap REAL,
            source TEXT DEFAULT 'x402',
            PRIMARY KEY (symbol, timestamp)
        );
        """
    )


def _store_snapshot(conn: sqlite3.Connection, ts: str, payload: dict) -> None:
    fgi = payload.get("fear_greed_index") or payload.get("fear_greed")
    if fgi is not None:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO fear_greed (timestamp, value, classification) VALUES (?, ?, ?)",
                (ts, float(fgi), str(payload.get("fear_greed_classification") or "")),
            )
        except (TypeError, ValueError):
            pass
    metrics = payload.get("market_metrics") or payload
    if isinstance(metrics, dict):
        conn.execute(
            "INSERT OR REPLACE INTO market_metrics (timestamp, btc_dominance, altcoin_volume, payload_json) VALUES (?, ?, ?, ?)",
            (
                ts,
                float(metrics.get("btc_dominance") or 0.0) if metrics.get("btc_dominance") is not None else None,
                float(metrics.get("altcoin_volume") or 0.0) if metrics.get("altcoin_volume") is not None else None,
                json.dumps(metrics),
            ),
        )
    for symbol, row in payload.items():
        if not isinstance(row, dict):
            continue
        # --- quotes table: persist per-symbol price/volume data ---
        price = row.get("price")
        pct_1h = row.get("percent_change_1h")
        pct_24h = row.get("percent_change_24h")
        vol_24h = row.get("volume_24h")
        vol_1h = row.get("volume_1h")
        mkt_cap = row.get("market_cap")
        if any(v is not None for v in (price, pct_1h, pct_24h, vol_24h, vol_1h, mkt_cap)):
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO quotes"
                    " (symbol, timestamp, price, percent_change_1h, percent_change_24h,"
                    "  volume_24h, volume_1h, market_cap, source)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(symbol).upper(),
                        ts,
                        float(price) if price is not None else None,
                        float(pct_1h) if pct_1h is not None else None,
                        float(pct_24h) if pct_24h is not None else None,
                        float(vol_24h) if vol_24h is not None else None,
                        float(vol_1h) if vol_1h is not None else None,
                        float(mkt_cap) if mkt_cap is not None else None,
                        "x402",
                    ),
                )
            except (TypeError, ValueError):
                pass
        # --- funding_rates table ---
        funding = row.get("funding_rate")
        oi = row.get("open_interest")
        if funding is None and oi is None:
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO funding_rates (token, timestamp, funding_rate, open_interest) VALUES (?, ?, ?, ?)",
                (
                    str(symbol).upper(),
                    ts,
                    float(funding) if funding is not None else None,
                    float(oi) if oi is not None else None,
                ),
            )
        except (TypeError, ValueError):
            continue


def main() -> int:
    settings = load_settings()
    if not getattr(settings, "cmc_collector_enabled", True):
        print("CMC collector disabled (CMC_COLLECTOR_ENABLED=false)")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc)
    stamp = ts.strftime("%Y-%m-%d_%H-%M")
    ts_iso = ts.isoformat()

    twak = TWAKInterface(paper_trade=settings.paper_trade)
    snapshot: dict = {"collected_at": ts_iso, "symbols": {}}

    try:
        from src.data.cmc_mcp_client import CMCMCPClient

        client = CMCMCPClient(settings)
        symbols = getattr(settings, "ml_universe_symbols", None) or TRADABLE_TARGET_SYMBOLS
        # Fetch free keyless snapshot first to harvest CMC ids.
        # The paid x402 tool rejects symbol-only requests ("id: Required"),
        # so id_overrides must be populated from the keyless layer — same
        # pattern used by the main trading loop.
        keyless = client.fetch_keyless_quotes_snapshot(symbols)
        id_overrides: dict[str, str] = {
            str(sym).upper(): str(row["id"])
            for sym, row in keyless.items()
            if isinstance(row, dict) and row.get("id")
        }
        enriched = client.fetch_x402_enriched_snapshot(symbols, id_overrides)
        if isinstance(enriched, dict) and enriched:
            snapshot["symbols"] = enriched
            snapshot.update({k: v for k, v in enriched.items() if isinstance(v, dict)})
            # Persist the x402 snapshot so the bot can load it on restart
            # without paying for another x402 call (same format as
            # DualMarketSnapshotCache._load_persisted / _save_persisted).
            _cache_payload = {
                "x402_enriched": enriched,
                "x402_fetched_at_epoch": time.time(),
            }
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _cache_tmp = CACHE_PATH.with_suffix(".tmp")
            _cache_tmp.write_text(json.dumps(_cache_payload), encoding="utf-8")
            _cache_tmp.replace(CACHE_PATH)
            # Update price_cache.json and volume_cache.json so the dashboard
            # market cache section populates independently of the breakout engine.
            _update_local_cache(
                PRICE_CACHE_PATH,
                {sym: data["price"] for sym, data in enriched.items() if isinstance(data, dict) and data.get("price") is not None},
            )
            _update_local_cache(
                VOLUME_CACHE_PATH,
                {sym: data["volume_24h"] for sym, data in enriched.items() if isinstance(data, dict) and data.get("volume_24h") is not None},
            )
    except Exception as exc:
        snapshot["error"] = str(exc)
        print(f"CMC fetch failed: {exc}")

    raw_path = RAW_DIR / f"{stamp}.json"
    raw_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    with sqlite3.connect(DB_PATH) as conn:
        _init_db(conn)
        _store_snapshot(conn, ts_iso, snapshot.get("symbols", snapshot))
        conn.commit()

    print(f"Wrote {raw_path} and updated {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
