"""Technical volatility utilities for 5-minute crypto data.

Example:
    cache = PriceCache(maxlen=2880)
    cache.add_ohlcv("CAKE", 100.0, 105.0, 95.0, 100.0, 50000.0, now)
    atr_pct = cache.get_atr_pct("CAKE", 14)

Interface contract:
    Imports: standard library only.
    Exports: true range, EMA, ATR helpers, PriceCache.
    Does not touch execution, wallets, settings, or live order flow.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque


@dataclass(frozen=True)
class OHLCV:
    """One UTC OHLCV observation."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


def calculate_true_range(high: float, low: float, prev_close: float) -> float:
    """Return Wilder true range using high/low and the previous close."""

    high_f = float(high)
    low_f = float(low)
    prev_f = float(prev_close)
    return float(max(high_f - low_f, abs(high_f - prev_f), abs(low_f - prev_f)))


def calculate_ema(values: list[float], period: int) -> float | None:
    """Return EMA seeded by the first-period SMA, or None until enough data."""

    if period <= 0 or len(values) < period:
        return None
    numeric = [float(value) for value in values]
    ema = sum(numeric[:period]) / period
    k = 2 / (period + 1)
    for value in numeric[period:]:
        ema = (value * k) + (ema * (1 - k))
    return float(ema)


def calculate_atr(true_ranges: list[float], period: int, smoothing: str = "ema") -> float | None:
    """Return ATR from true ranges using EMA or SMA smoothing."""

    if period <= 0 or len(true_ranges) < period:
        return None
    numeric = [float(value) for value in true_ranges]
    if smoothing == "sma":
        return float(sum(numeric[-period:]) / period)
    if smoothing != "ema":
        return None
    return calculate_ema(numeric, period)


def calculate_atr_pct(atr: float, close: float) -> float | None:
    """Return ATR divided by close as a decimal percentage."""

    close_f = float(close)
    if close_f <= 0:
        return None
    return float(atr) / close_f


class PriceCache:
    """FIFO in-memory OHLCV cache with bounded per-symbol history."""

    def __init__(self, maxlen: int = 2880) -> None:
        self.maxlen = max(1, int(maxlen))
        self._data: dict[str, Deque[OHLCV]] = defaultdict(lambda: deque(maxlen=self.maxlen))

    def add_ohlcv(
        self,
        symbol: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: datetime,
    ) -> None:
        """Append one OHLCV point, normalizing the timestamp to UTC."""

        normalized_symbol = symbol.upper()
        normalized_timestamp = self._normalize_timestamp(timestamp)
        self._data[normalized_symbol].append(
            OHLCV(
                open=float(open_price),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
                timestamp=normalized_timestamp,
            )
        )

    def get_ema(self, symbol: str, periods: int) -> float | None:
        """Return close-price EMA for a symbol, fail-closed until enough data."""

        closes = [item.close for item in self._data.get(symbol.upper(), ())]
        return calculate_ema(closes, periods)

    def get_atr(self, symbol: str, periods: int, smoothing: str = "ema") -> float | None:
        """Return ATR for a symbol, fail-closed until enough data."""

        points = list(self._data.get(symbol.upper(), ()))
        if len(points) < periods:
            return None
        true_ranges: list[float] = []
        previous_close = points[0].close
        for point in points:
            true_ranges.append(calculate_true_range(point.high, point.low, previous_close))
            previous_close = point.close
        return calculate_atr(true_ranges, periods, smoothing)

    def get_atr_pct(self, symbol: str, periods: int) -> float | None:
        """Return ATR percentage using the latest close as denominator."""

        points = list(self._data.get(symbol.upper(), ()))
        if not points:
            return None
        atr = self.get_atr(symbol, periods)
        if atr is None:
            return None
        return calculate_atr_pct(atr, points[-1].close)

    def has_sufficient_data(self, symbol: str, periods: int) -> bool:
        """Return whether the cache has at least periods observations."""

        return len(self._data.get(symbol.upper(), ())) >= periods

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)
