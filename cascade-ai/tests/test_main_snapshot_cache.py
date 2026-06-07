"""Tests for cached market snapshot loading in main."""

from __future__ import annotations

from typing import Any

import src.main as main_module
from src.config.settings import Settings
from src.data.market_snapshot_cache import get_market_snapshot_cache


class FakeCMCClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_market_snapshot(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self.calls += 1
        return {"CAKE": {"symbol": "CAKE", "price": float(self.calls)}}


def test_fetch_snapshot_reuses_cmc_client_within_ttl() -> None:
    get_market_snapshot_cache().reset()
    settings = Settings(paper_trade=False, cmc_snapshot_ttl_seconds=7200)
    client = FakeCMCClient()

    first = main_module._fetch_snapshot(settings, client)  # type: ignore[arg-type]
    second = main_module._fetch_snapshot(settings, client)  # type: ignore[arg-type]

    assert first == {"CAKE": {"symbol": "CAKE", "price": 1.0}}
    assert second == first
    assert client.calls == 1
    get_market_snapshot_cache().reset()
