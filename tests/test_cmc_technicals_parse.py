"""Tests for x402 technicals parsing and per-id fetching.

The CMC x402 technical-analysis tool is single-asset and returns RSI/MACD as
nested objects with comma-grouped string numbers. These tests lock in that the
client fetches per-id (the batched comma-joined call is rejected by the server),
maps each blob to its symbol, and parses rsi.rsi14 / macd.macdLine correctly.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.data.cmc_mcp_client import CMCMCPClient

# The exact shape the live CMC x402 server returned for ETH (id 1027).
ETH_TECHNICALS = {
    "moving_averages": {"simple_moving_average_7_day": "1,706.82"},
    "macd": {"macdLine": "-94.82", "signalLine": "-118.75", "histogram": "23.93"},
    "rsi": {"rsi7": "59.27", "rsi14": "44.87", "rsi21": "41.72"},
    "pivotPoint": "1795.24",
}


def test_coerce_number_handles_comma_grouped_strings() -> None:
    assert CMCMCPClient._coerce_number("1,706.82") == 1706.82
    assert CMCMCPClient._coerce_number("44.87") == 44.87
    assert CMCMCPClient._coerce_number(12.5) == 12.5
    assert CMCMCPClient._coerce_number("n/a") is None
    assert CMCMCPClient._coerce_number({"rsi14": "1"}) is None


def test_rsi_and_macd_parsed_from_nested_technicals() -> None:
    client = CMCMCPClient(Settings())
    technicals = {"ETH": ETH_TECHNICALS}
    quotes = {"ETH": {"symbol": "ETH", "price": "1,700.5", "volume_24h": 1_000_000_000, "market_cap": 2e11}}

    snapshot = client._build_enriched_snapshot(["ETH"], quotes, technicals, {}, {})

    row = snapshot["ETH"]
    assert row["rsi"] == 44.87  # rsi.rsi14, not the nested dict
    assert row["macd"] == -94.82  # macd.macdLine
    assert row["price"] == 1700.5  # comma-grouped string parsed


def test_technicals_fetched_per_id_and_capped() -> None:
    client = CMCMCPClient(Settings(x402_technicals_max_symbols=2))
    calls: list[dict[str, str]] = []

    def fake_call(tool_name: str, arguments: dict[str, str]) -> dict[str, object]:
        calls.append(arguments)
        return dict(ETH_TECHNICALS)

    client._call_tool_x402 = fake_call  # type: ignore[assignment]

    # ETH/CAKE/LINK all have pinned CMC ids; cap=2 must stop after two calls.
    result = client._fetch_x402_technicals_id_preferred(["ETH", "CAKE", "LINK"])

    assert len(calls) == 2
    assert all("," not in args["id"] for args in calls)  # never comma-batched
    assert set(result) == {"ETH", "CAKE"}
    assert result["ETH"]["rsi"]["rsi14"] == "44.87"


def test_technicals_stop_when_budget_exhausted() -> None:
    client = CMCMCPClient(Settings(x402_technicals_max_symbols=0))  # no cap

    def empty_call(tool_name: str, arguments: dict[str, str]) -> dict[str, object]:
        return {}  # governor exhausted -> _call_tool_x402 returns {}

    client._call_tool_x402 = empty_call  # type: ignore[assignment]
    result = client._fetch_x402_technicals_id_preferred(["ETH", "CAKE"])
    assert result == {}  # nothing populated, degrades gracefully
