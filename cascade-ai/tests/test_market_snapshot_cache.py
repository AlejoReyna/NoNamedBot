"""Tests for CMC market snapshot TTL cache."""

from __future__ import annotations

from src.data.market_snapshot_cache import MarketSnapshotCache


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
