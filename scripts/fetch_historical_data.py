#!/usr/bin/env python3
"""Fetch historical Binance OHLCV and CMC premium snapshots for ML training."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import tenacity

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import load_settings
from src.config.tokens import ELIGIBLE_149_SYMBOLS, MOMENTUM_EXCLUDED
from src.data.binance_client import BinanceClient


# ---------------------------------------------------------------------------
# CMC client loader
# ---------------------------------------------------------------------------

def _load_cmc_client(settings):
    try:
        from src.data.cmc_mcp_client import CMCMCPClient

        return CMCMCPClient(settings)
    except Exception as exc:
        print(f"CMC client unavailable ({exc}); skipping premium snapshots.")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedupe_symbols(symbols: list[str]) -> list[str]:
    """Preserve order, dedupe, strip whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        key = s.strip().upper()
        if key not in seen:
            seen.add(key)
            out.append(s.strip())
    return out


def _load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _http_status_from_error(exc: Exception) -> int | None:
    """Extract HTTP status from requests or tenacity-wrapped errors."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    if isinstance(exc, tenacity.RetryError):
        # Unwrap the last underlying exception
        cause = exc.__cause__
        if isinstance(cause, requests.HTTPError) and cause.response is not None:
            return cause.response.status_code
    return None


# ---------------------------------------------------------------------------
# Per-symbol fetch worker
# ---------------------------------------------------------------------------

def _fetch_symbol(
    symbol: str,
    days: int,
    client: BinanceClient,
    binance_dir: Path,
    resume_ts: datetime | None,
    existing_path: Path | None,
) -> dict | None:
    """Fetch OHLCV for one symbol, append when resuming, save parquet, return manifest entry."""
    try:
        # Determine how many days we actually need to pull
        fetch_days = days
        if resume_ts is not None:
            elapsed = (_now_utc() - resume_ts).total_seconds() / 86400
            if elapsed <= 0:
                # Already up-to-date
                return None
            fetch_days = max(1, int(elapsed)) + 1  # +1 day overlap buffer

        frame = client.fetch_history_days(symbol, days=fetch_days, interval="15m")
        if frame.empty:
            print(f"  [SKIP] {symbol}: empty DataFrame (pair not on Binance)")
            return None

        # Append / dedupe when resuming
        if existing_path is not None and existing_path.exists():
            old = pd.read_parquet(existing_path)
            frame = pd.concat([old, frame], ignore_index=True)
            frame = (
                frame.drop_duplicates(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )

        out_path = binance_dir / f"ohlcv_15m_{symbol.upper()}.parquet"
        frame.to_parquet(out_path, index=False)

        return {
            "rows": len(frame),
            "path": str(out_path),
            "last_fetched_timestamp": frame["timestamp"].iloc[-1].isoformat(),
        }
    except (requests.HTTPError, tenacity.RetryError) as exc:
        status = _http_status_from_error(exc)
        if status == 404:
            print(f"  [SKIP] {symbol}: 404 (pair not on Binance)")
        elif status is not None:
            print(f"  [SKIP] {symbol}: HTTP {status} (pair unavailable)")
        else:
            print(f"  [ERROR] {symbol}: {exc}")
        return None
    except Exception as exc:
        print(f"  [ERROR] {symbol}: {exc}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch historical Binance OHLCV and CMC premium snapshots."
    )
    # Default = all 149 deduped (filtering happens after parsing regardless of input)
    _default_symbols = list(dict.fromkeys(ELIGIBLE_149_SYMBOLS))
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=_default_symbols,
        help="Token symbols to fetch (default: full 149 eligible universe)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Days of OHLCV history to fetch (default: 180)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel fetch workers (default: 4)",
    )
    parser.add_argument(
        "--binance-only",
        action="store_true",
        help="Skip CMC premium snapshots",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Setup dirs / clients
    # ------------------------------------------------------------------
    settings = load_settings()
    cmc_client = None if args.binance_only else _load_cmc_client(settings)

    binance_dir = Path("data/historical/binance")
    cmc_dir = Path("data/historical/cmc")
    binance_dir.mkdir(parents=True, exist_ok=True)
    cmc_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Filter symbols
    # ------------------------------------------------------------------
    symbols = _dedupe_symbols(args.symbols)
    skipped_excluded: list[str] = []
    filtered: list[str] = []
    for s in symbols:
        if s.upper() in MOMENTUM_EXCLUDED:
            skipped_excluded.append(s)
        else:
            filtered.append(s)
    symbols = filtered

    if skipped_excluded:
        print(f"Excluded {len(skipped_excluded)} stable/gold symbols: {skipped_excluded}")

    # ------------------------------------------------------------------
    # Load existing manifest for resume
    # ------------------------------------------------------------------
    manifest_path = binance_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest["fetched_at"] = _now_utc().isoformat()
    manifest["days"] = args.days
    manifest.setdefault("symbols", {})

    # ------------------------------------------------------------------
    # Parallel OHLCV fetch
    # ------------------------------------------------------------------
    total_rows = 0
    fetched_count = 0
    failed_symbols: list[str] = []

    def _worker(symbol: str) -> tuple[str, dict | None]:
        # One client per worker (request_delay_s=0.1 is already built-in)
        client = BinanceClient()
        resume_ts: datetime | None = None
        existing_path: Path | None = None

        entry = manifest["symbols"].get(symbol.upper())
        if entry and entry.get("last_fetched_timestamp"):
            try:
                resume_ts = datetime.fromisoformat(entry["last_fetched_timestamp"])
                existing_path = binance_dir / f"ohlcv_15m_{symbol.upper()}.parquet"
            except Exception:
                resume_ts = None
                existing_path = None

        result = _fetch_symbol(symbol, args.days, client, binance_dir, resume_ts, existing_path)
        return symbol, result

    print(f"Fetching OHLCV for {len(symbols)} symbols with {args.workers} workers …")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_worker, s): s for s in symbols}
        for future in concurrent.futures.as_completed(futures):
            symbol, result = future.result()
            if result is None:
                failed_symbols.append(symbol)
            else:
                manifest["symbols"][symbol.upper()] = result
                total_rows += result["rows"]
                fetched_count += 1
                print(f"  [OK] {symbol}: {result['rows']} rows")

    # ------------------------------------------------------------------
    # CMC daily snapshot loop (same date range)
    # ------------------------------------------------------------------
    snapshot_rows: list[dict[str, object]] = []
    if cmc_client is not None and symbols:
        end = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=args.days)
        day = start
        while day <= end:
            print(f"Fetching CMC premium snapshot for {day.date()} …")
            try:
                snapshot = cmc_client.fetch_x402_enriched_snapshot(symbols)
            except Exception as exc:
                print(f"  CMC fetch failed: {exc}")
                snapshot = {}
            ts = day.isoformat()
            for sym, payload in snapshot.items():
                if not isinstance(payload, dict):
                    continue
                row = {"timestamp": ts, "symbol": sym.upper(), **payload}
                snapshot_rows.append(row)
            day += timedelta(days=1)

    cmc_path = cmc_dir / "premium_snapshots.parquet"
    if snapshot_rows:
        pd.DataFrame(snapshot_rows).to_parquet(cmc_path, index=False)
        print(f"Wrote CMC snapshots ({len(snapshot_rows)} rows) to {cmc_path}")
    manifest["cmc_rows"] = len(snapshot_rows)

    # ------------------------------------------------------------------
    # Save manifest
    # ------------------------------------------------------------------
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    print(f"Wrote manifest to {manifest_path}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    skipped_all = skipped_excluded + failed_symbols
    print(
        f"\nFetched {fetched_count} symbols, {total_rows} total rows, "
        f"skipped: {skipped_all}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
