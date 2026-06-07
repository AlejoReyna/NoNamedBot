"""Tests for CMC market snapshot TTL cache."""

from __future__ import annotations

from src.data.market_snapshot_cache import (
    DualMarketSnapshotCache,
    MarketSnapshotCache,
    merge_market_snapshots,
)


def test_get_or_fetch_calls_fetcher_once_within_ttl() -> None:
    cache = MarketSnapshotCache()
    calls = {"count": 0}

    def fetcher() -> dict[str, dict[str, object]]:
        calls["count"] += 1
        return {"CAKE": {"symbol": "CAKE", "price": 2.0}}

    first = cache.get_or_fetch(7200, fetcher)
    second = cache.get_or_fetch(7200, fetcher)

    assert first == {"CAKE": {"symbol": "CAKE", "price": 2.0}}
    assert second == first
    assert calls["count"] == 1


def test_get_or_fetch_refreshes_after_ttl(monkeypatch: object) -> None:
    cache = MarketSnapshotCache()
    now = {"value": 1000.0}
    monkeypatch.setattr("src.data.market_snapshot_cache.time.monotonic", lambda: now["value"])  # type: ignore[attr-defined]

    def fetcher() -> dict[str, dict[str, object]]:
        return {"BNB": {"symbol": "BNB", "price": now["value"]}}

    cache.get_or_fetch(7200, fetcher)
    now["value"] += 7200
    refreshed = cache.get_or_fetch(7200, fetcher)

    assert refreshed == {"BNB": {"symbol": "BNB", "price": 8200.0}}


def test_get_or_fetch_ttl_zero_always_refreshes() -> None:
    cache = MarketSnapshotCache()
    calls = {"count": 0}

    def fetcher() -> dict[str, dict[str, object]]:
        calls["count"] += 1
        return {"CAKE": {"symbol": "CAKE", "price": float(calls["count"])}}

    cache.get_or_fetch(0, fetcher)
    cache.get_or_fetch(0, fetcher)

    assert calls["count"] == 2


def test_get_or_fetch_returns_copy_so_mutations_do_not_affect_cache() -> None:
    cache = MarketSnapshotCache()

    def fetcher() -> dict[str, dict[str, object]]:
        return {"CAKE": {"symbol": "CAKE", "price": 2.0}}

    snapshot = cache.get_or_fetch(7200, fetcher)
    snapshot["CAKE"]["price"] = 99.0
    cached = cache.get_or_fetch(7200, fetcher)

    assert cached["CAKE"]["price"] == 2.0


def test_merge_market_snapshots_overlays_hot_fields_only() -> None:
    base = {
        "CAKE": {
            "symbol": "CAKE",
            "price": 1.0,
            "estimated_slippage_pct": 0.002,
            "rsi": 55.0,
        }
    }
    overlay = {
        "CAKE": {
            "symbol": "CAKE",
            "price": 2.0,
            "percent_change_1h": 0.01,
            "estimated_slippage_pct": 0.9,
        }
    }

    merged = merge_market_snapshots(base, overlay)

    assert merged["CAKE"]["price"] == 2.0
    assert merged["CAKE"]["percent_change_1h"] == 0.01
    assert merged["CAKE"]["estimated_slippage_pct"] == 0.9
    assert merged["CAKE"]["rsi"] == 55.0


def test_dual_cache_refreshes_keyless_more_often_than_x402(monkeypatch: object) -> None:
    cache = DualMarketSnapshotCache()
    now = {"value": 1000.0}
    monkeypatch.setattr("src.data.market_snapshot_cache.time.monotonic", lambda: now["value"])  # type: ignore[attr-defined]
    x402_calls = {"count": 0}
    keyless_calls = {"count": 0}

    def x402_fetcher() -> dict[str, dict[str, object]]:
        x402_calls["count"] += 1
        return {"CAKE": {"symbol": "CAKE", "price": 1.0, "estimated_slippage_pct": 0.002}}

    def keyless_fetcher() -> dict[str, dict[str, object]]:
        keyless_calls["count"] += 1
        return {"CAKE": {"symbol": "CAKE", "price": float(keyless_calls["count"])}}

    first = cache.get_merged_snapshot(7200, 300, x402_fetcher, keyless_fetcher)
    now["value"] += 300
    second = cache.get_merged_snapshot(7200, 300, x402_fetcher, keyless_fetcher)

    assert first["CAKE"]["price"] == 1.0
    assert second["CAKE"]["price"] == 2.0
    assert second["CAKE"]["estimated_slippage_pct"] == 0.002
    assert x402_calls["count"] == 1
    assert keyless_calls["count"] == 2


def test_dual_cache_reset_clears_both_layers() -> None:
    cache = DualMarketSnapshotCache()
    cache.get_merged_snapshot(
        7200,
        300,
        lambda: {"CAKE": {"symbol": "CAKE", "price": 1.0}},
        lambda: {"CAKE": {"symbol": "CAKE", "price": 2.0}},
    )
    cache.reset()
    calls = {"count": 0}

    def fetcher() -> dict[str, dict[str, object]]:
        calls["count"] += 1
        return {"CAKE": {"symbol": "CAKE", "price": 3.0}}

    cache.get_merged_snapshot(7200, 300, fetcher, fetcher)

    assert calls["count"] == 2
