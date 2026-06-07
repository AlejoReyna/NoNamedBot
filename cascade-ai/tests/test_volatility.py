"""Tests for technical volatility helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from src.strategy.volatility import (
    PriceCache,
    calculate_atr,
    calculate_atr_pct,
    calculate_ema,
    calculate_true_range,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_true_range_with_gap_from_prev_close() -> None:
    assert calculate_true_range(105, 104, 100) == 5.0


def test_true_range_without_gap() -> None:
    assert calculate_true_range(105, 104, 103) == 2.0


def test_true_range_inside_previous_close_edge() -> None:
    assert calculate_true_range(105, 104, 104.5) == 1.0


def test_ema_returns_none_until_enough_data() -> None:
    cache = PriceCache()
    for i in range(50):
        cache.add_ohlcv("CAKE", 100 + i, 100 + i, 100 + i, 100 + i, 1000, _now())
    assert cache.get_ema("CAKE", periods=288) is None


def test_ema_weights_recent_more_than_old() -> None:
    cache = PriceCache()
    prices = [100.0] * 287 + [110.0]
    for price in prices:
        cache.add_ohlcv("CAKE", price, price, price, price, 1000, _now())
    ema = cache.get_ema("CAKE", 288)
    assert ema is not None
    assert ema > 100.0
    assert ema < 110.0


def test_calculate_ema_rejects_invalid_period_edge() -> None:
    assert calculate_ema([1.0, 2.0], 0) is None


def test_atr_returns_none_without_sufficient_data() -> None:
    cache = PriceCache()
    for _ in range(5):
        cache.add_ohlcv("CAKE", 100, 105, 95, 100, 1000, _now())
    assert cache.get_atr("CAKE", 14) is None


def test_atr_pct_is_atr_divided_by_close() -> None:
    cache = PriceCache()
    for _ in range(20):
        cache.add_ohlcv("CAKE", 100, 105, 95, 100, 1000, _now())
    atr = cache.get_atr("CAKE", 14)
    assert atr is not None
    assert abs(cache.get_atr_pct("CAKE", 14) - (atr / 100.0)) < 1e-9  # type: ignore[operator]


def test_atr_pct_returns_none_for_zero_close_edge() -> None:
    assert calculate_atr_pct(1.0, 0.0) is None


def test_calculate_atr_sma_happy_path() -> None:
    assert calculate_atr([1.0, 2.0, 3.0], 3, smoothing="sma") == 2.0


def test_calculate_atr_unknown_smoothing_edge() -> None:
    assert calculate_atr([1.0, 2.0, 3.0], 3, smoothing="bad") is None


def test_price_cache_fifo_maxlen() -> None:
    cache = PriceCache(maxlen=100)
    for i in range(200):
        cache.add_ohlcv("CAKE", i, i, i, i, 1000, _now())
    assert len(cache._data["CAKE"]) == 100


def test_price_cache_normalizes_naive_timestamp_to_utc_edge() -> None:
    cache = PriceCache()
    cache.add_ohlcv("CAKE", 1, 1, 1, 1, 1, datetime(2026, 1, 1))
    assert cache._data["CAKE"][0].timestamp.tzinfo is timezone.utc


def test_has_sufficient_data_happy_and_edge() -> None:
    cache = PriceCache()
    cache.add_ohlcv("CAKE", 1, 1, 1, 1, 1, _now())
    assert cache.has_sufficient_data("CAKE", 1) is True
    assert cache.has_sufficient_data("CAKE", 2) is False
