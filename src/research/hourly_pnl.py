"""Hourly PnL tracker for competition scoring.

The competition measures returns hour-by-hour. This module:
  - writes one record to logs/hourly_pnl.jsonl each time the UTC hour rolls over
  - backfills missing hours from the existing portfolio_snapshots.jsonl
  - exposes read_hourly_pnl() for display / API consumption
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PATH = "logs/hourly_pnl.jsonl"
SNAPSHOTS_PATH = "logs/portfolio_snapshots.jsonl"


def _hour_str(dt: datetime) -> str:
    """Truncate a datetime to the UTC hour as an ISO string."""
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read_hourly_pnl(path: str | Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """Return all hourly PnL records sorted oldest-first."""
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    records.sort(key=lambda r: r.get("hour", ""))
    return records


def _hours_already_written(path: Path) -> set[str]:
    return {r.get("hour", "") for r in read_hourly_pnl(path)}


def write_hourly_record(
    portfolio_value_usdc: float,
    prev_hour_value_usdc: float | None,
    hour: str,
    open_position_count: int = 0,
    path: str | Path = DEFAULT_PATH,
) -> None:
    pnl_usdc = (portfolio_value_usdc - prev_hour_value_usdc) if prev_hour_value_usdc is not None else None
    pnl_pct = (pnl_usdc / prev_hour_value_usdc * 100) if (pnl_usdc is not None and prev_hour_value_usdc) else None
    record = {
        "hour": hour,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_value_usdc": portfolio_value_usdc,
        "prev_hour_value_usdc": prev_hour_value_usdc,
        "pnl_usdc": pnl_usdc,
        "pnl_pct": pnl_pct,
        "open_position_count": open_position_count,
    }
    _append(Path(path), record)
    LOGGER.info(
        "Hourly PnL [%s]: $%.4f (%s)",
        hour,
        portfolio_value_usdc,
        f"{pnl_pct:+.2f}%" if pnl_pct is not None else "first hour",
    )


def backfill_from_snapshots(
    snapshots_path: str | Path = SNAPSHOTS_PATH,
    hourly_pnl_path: str | Path = DEFAULT_PATH,
) -> int:
    """Compute hourly PnL from portfolio_snapshots.jsonl for any hours not yet recorded.

    Takes the FIRST snapshot value in each UTC hour as that hour's opening value,
    then computes pnl vs the previous hour's opening value — which matches the
    competition's hour-by-hour return methodology.

    Returns the number of hours backfilled.
    """
    sp = Path(snapshots_path)
    if not sp.exists():
        return 0

    # Collect first snapshot value per hour
    hour_first: dict[str, float] = {}
    for line in sp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = row.get("timestamp")
        val = row.get("portfolio_value_usdc")
        if not ts_str or val is None:
            continue
        dt = _parse_ts(ts_str)
        if dt is None:
            continue
        h = _hour_str(dt)
        if h not in hour_first:
            hour_first[h] = float(val)

    if not hour_first:
        return 0

    already = _hours_already_written(Path(hourly_pnl_path))
    sorted_hours = sorted(hour_first)
    filled = 0
    for i, hour in enumerate(sorted_hours):
        if hour in already:
            continue
        prev_val = hour_first[sorted_hours[i - 1]] if i > 0 else None
        write_hourly_record(
            portfolio_value_usdc=hour_first[hour],
            prev_hour_value_usdc=prev_val,
            hour=hour,
            path=hourly_pnl_path,
        )
        filled += 1

    if filled:
        LOGGER.info("Hourly PnL: backfilled %d hours from portfolio snapshots", filled)
    return filled


class HourlyPnlTracker:
    """Stateful per-process tracker called each trading cycle.

    Call .maybe_record(portfolio_value, open_position_count) every cycle.
    It writes a record to hourly_pnl.jsonl exactly once when the UTC hour rolls over.
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._current_hour: str | None = None
        self._prev_value: float | None = None
        self._initialized = False

    def _load_last_known(self) -> None:
        """Bootstrap prev_value from the last written record so restarts don't lose history."""
        records = read_hourly_pnl(self._path)
        if records:
            last = records[-1]
            self._current_hour = last.get("hour")
            self._prev_value = last.get("portfolio_value_usdc")
        self._initialized = True

    def maybe_record(self, portfolio_value: float, open_position_count: int = 0) -> bool:
        """Write a record if the UTC hour has rolled over. Returns True when written."""
        if not self._initialized:
            self._load_last_known()

        now = datetime.now(timezone.utc)
        this_hour = _hour_str(now)

        if this_hour == self._current_hour:
            return False

        write_hourly_record(
            portfolio_value_usdc=portfolio_value,
            prev_hour_value_usdc=self._prev_value,
            hour=this_hour,
            open_position_count=open_position_count,
            path=self._path,
        )
        self._prev_value = portfolio_value
        self._current_hour = this_hour
        return True
