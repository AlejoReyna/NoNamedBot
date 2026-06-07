"""Tests for deterministic live sentiment tier 1."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from src.strategy.sentiment_tier1 import SentimentTier1


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _sentiment(fgi: int | None = None, funding: float | None = None, gas: float | None = None) -> SentimentTier1:
    sentiment = SentimentTier1()
    sentiment.get_fear_greed = lambda: None if fgi is None else {"value": fgi, "classification": "x"}  # type: ignore[method-assign]
    sentiment.get_derivatives_metrics = lambda: {"funding_rate_avg": funding, "total_open_interest": 10.0}  # type: ignore[method-assign]
    sentiment._fetch_gas_price_gwei = lambda: gas  # type: ignore[method-assign]
    return sentiment


def test_extreme_greed_returns_negative_delta() -> None:
    result = _sentiment(fgi=80, funding=None, gas=0.1).compute_sentiment()
    assert result.sentiment_delta < 0
    assert result.regime_fragility == "EXTREME_GREED"


def test_extreme_fear_returns_small_positive_delta() -> None:
    result = _sentiment(fgi=15, funding=None, gas=0.1).compute_sentiment()
    assert result.sentiment_delta > 0
    assert result.regime_fragility == "EXTREME_FEAR"


def test_crowded_long_funding_returns_negative_delta() -> None:
    result = _sentiment(fgi=None, funding=0.002, gas=0.1).compute_sentiment()
    assert result.sentiment_delta < -0.5
    assert result.regime_fragility == "CROWDED_LONG"


def test_crowded_short_funding_returns_positive_edge() -> None:
    result = _sentiment(fgi=None, funding=-0.001, gas=0.1).compute_sentiment()
    assert result.sentiment_delta == 0.5
    assert result.regime_fragility == "CROWDED_SHORT"


def test_combined_extremes_clamped() -> None:
    result = _sentiment(fgi=85, funding=0.002, gas=1.0).compute_sentiment()
    assert result.sentiment_delta >= -2.5
    assert result.sentiment_delta <= 1.0


def test_neutral_returns_zero_delta() -> None:
    result = _sentiment(fgi=50, funding=None, gas=0.1).compute_sentiment()
    assert result.sentiment_delta == 0.0
    assert result.regime_fragility == "NONE"


def test_api_failure_returns_neutral() -> None:
    sentiment = SentimentTier1()
    sentiment._fetch_json = lambda _endpoint: None  # type: ignore[method-assign]
    result = sentiment.compute_sentiment()
    assert result.sentiment_delta == 0.0
    assert result.regime_fragility == "NONE"
    assert result.fear_greed_index is None


def test_cache_reduces_api_calls(monkeypatch: object) -> None:
    calls = {"count": 0}

    def fake_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        calls["count"] += 1
        return FakeResponse({"data": [{"value": 55, "value_classification": "Neutral"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)  # type: ignore[attr-defined]
    sentiment = SentimentTier1(cache_ttl_seconds=300)
    assert sentiment._fetch_json("/v3/fear-and-greed/latest") is not None
    assert sentiment._fetch_json("/v3/fear-and-greed/latest") is not None
    assert calls["count"] == 1


def test_get_fear_greed_handles_bad_payload_edge() -> None:
    sentiment = SentimentTier1()
    sentiment._fetch_json = lambda _endpoint: {"data": []}  # type: ignore[method-assign]
    assert sentiment.get_fear_greed() is None


def test_get_fear_greed_happy_path() -> None:
    sentiment = SentimentTier1()
    sentiment._fetch_json = lambda _endpoint: {  # type: ignore[method-assign]
        "data": [{"value": "78", "value_classification": "Extreme Greed"}]
    }
    assert sentiment.get_fear_greed() == {"value": 78, "classification": "Extreme Greed"}


def test_get_derivatives_metrics_happy_path() -> None:
    sentiment = SentimentTier1()
    sentiment._fetch_json = lambda _endpoint: {  # type: ignore[method-assign]
        "data": {"total_open_interest": "123.4", "funding_rate_avg": "0.0012"}
    }
    result = sentiment.get_derivatives_metrics()
    assert result is not None
    assert result["total_open_interest"] == 123.4
    assert result["funding_rate_avg"] == 0.0012


def test_get_derivatives_metrics_bad_payload_edge() -> None:
    sentiment = SentimentTier1()
    sentiment._fetch_json = lambda _endpoint: {"data": []}  # type: ignore[method-assign]
    assert sentiment.get_derivatives_metrics() is None
