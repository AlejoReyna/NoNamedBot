"""Regression test for the BNB regime reference data path.

The keyless quotes API nests price/percent_change under quote.USD. If those are
not flattened, every BNB field reads None, the regime detector scores 0.0, and
the bot is stuck in permanent risk_off and never enters. This locks in the
flattening so that regression cannot recur silently.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import Settings
from src.data.cmc_mcp_client import CMCMCPClient
from src.main import _ensure_bnb_reference

# Exactly the nesting the live keyless API returns for BNB (id 1839).
KEYLESS_BNB_RAW = {
    "data": {
        "BNB": {
            "symbol": "BNB",
            "tags": ["marketplace", "layer-1"],
            "quote": {
                "USD": {
                    "price": 655.4,
                    "volume_24h": 1_200_000_000.0,
                    "market_cap": 95_000_000_000.0,
                    "percent_change_1h": 0.35,
                    "percent_change_24h": -1.2,
                }
            },
        }
    }
}


def test_ensure_bnb_reference_flattens_quote_usd_fields() -> None:
    client = CMCMCPClient(Settings())
    client._fetch_keyless = lambda tool, args: KEYLESS_BNB_RAW  # type: ignore[assignment]

    snapshot: dict[str, dict[str, Any]] = {}
    _ensure_bnb_reference(snapshot, client)

    assert "BNB" in snapshot, "BNB reference must be injected for the regime detector"
    bnb = snapshot["BNB"]
    assert bnb["price"] == 655.4
    assert bnb["percent_change_1h"] == 0.35
    assert bnb["percent_change_24h"] == -1.2
    assert bnb["volume_24h"] == 1_200_000_000.0


def test_ensure_bnb_reference_prefers_existing_wbnb() -> None:
    client = CMCMCPClient(Settings())
    snapshot = {"WBNB": {"symbol": "WBNB", "price": 650.0, "percent_change_1h": 0.1}}
    _ensure_bnb_reference(snapshot, client)
    assert snapshot["BNB"]["price"] == 650.0
