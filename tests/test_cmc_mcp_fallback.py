"""Tests for fail-open routing from optional CMC MCP to Keyless data."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.data.market_data_router import MarketDataRouter


class FailingMcp:
    enabled = True
    shadow_mode = False

    async def get_crypto_quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict[str, Any]:
        raise RuntimeError("x402 unavailable")


class FakeKeyless:
    def get_crypto_quotes_latest(self, symbols: list[str]) -> dict[str, Any]:
        return {"source": "keyless", "symbols": symbols}


def test_mcp_failure_falls_back_to_keyless(caplog: Any) -> None:
    caplog.set_level(logging.WARNING)
    router = MarketDataRouter(keyless_client=FakeKeyless(), mcp_client=FailingMcp())

    result = asyncio.run(router.get_quotes_latest(["BNB"]))

    assert result == {"source": "keyless", "symbols": ["BNB"]}
    assert "[CMC_MCP_FALLBACK]" in caplog.text
