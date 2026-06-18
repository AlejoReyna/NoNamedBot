"""Live TIER 1 sentiment as a deterministic soft contrarian filter.

Example:
    sentiment = SentimentTier1(bsc_rpc_url=settings.bsc_rpc_url or "")
    result = sentiment.compute_sentiment()

Interface contract:
    Imports: standard library only.
    Exports: SentimentResult and SentimentTier1.
    Does not execute trades, change sizing directly, or call shadow modules.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class SentimentResult:
    """Raw sentiment values plus deterministic regime-score modifier."""

    fear_greed_index: Optional[int]
    fear_greed_classification: Optional[str]
    funding_rate_btc: Optional[float]
    open_interest_btc: Optional[float]
    gas_price_gwei: Optional[float]
    gas_avg_24h_gwei: Optional[float]
    sentiment_delta: float
    regime_fragility: str
    source: str = "LIVE"


class SentimentTier1:
    """CMC Fear & Greed, CMC derivatives, and BSC gas price for live scoring."""

    def __init__(
        self,
        cmc_keyless_base: str = "https://pro-api.coinmarketcap.com/trial-pro-api",
        bsc_rpc_url: str = "",
        cache_ttl_seconds: int = 300,
        cmc_mcp_client: Any | None = None,
        sentiment_tier2: Any | None = None,
    ) -> None:
        self.cmc_base = cmc_keyless_base.rstrip("/")
        self.bsc_rpc = bsc_rpc_url
        self.cache_ttl = max(0, int(cache_ttl_seconds))
        self._cache: dict[str, tuple[float, dict]] = {}
        self.cmc_mcp_client = cmc_mcp_client
        self.sentiment_tier2 = sentiment_tier2

    def _fetch_json(self, endpoint: str) -> Optional[dict]:
        """Fetch JSON with a per-endpoint in-memory TTL cache."""

        cache_key = endpoint
        now = datetime.now(timezone.utc).timestamp()
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached_at, data = cached
            if now - cached_at < self.cache_ttl:
                return data
        try:
            request = urllib.request.Request(f"{self.cmc_base}{endpoint}")
            request.add_header("Accept", "application/json")
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            return None
        if isinstance(data, dict):
            self._cache[cache_key] = (now, data)
            return data
        return None

    def _fetch_gas_price_gwei(self) -> Optional[float]:
        """Fetch BSC eth_gasPrice via injected RPC URL."""

        if not self.bsc_rpc:
            return None
        payload = {"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1}
        try:
            request = urllib.request.Request(
                self.bsc_rpc,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            hex_price = str(result.get("result", "0x0"))
            return int(hex_price, 16) / 1e9
        except (OSError, TimeoutError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            return None

    def get_fear_greed(self) -> Optional[dict]:
        """Return CMC Fear & Greed as {'value': int, 'classification': str}."""

        data = self._fetch_json("/v3/fear-and-greed/latest")
        if not data:
            return None
        latest = self._first_record(data.get("data"))
        if latest is None:
            return None
        try:
            return {
                "value": int(latest["value"]),
                "classification": str(latest["value_classification"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def get_derivatives_metrics(self) -> Optional[dict]:
        """Return CMC global derivatives-like metrics when present."""

        data = self._fetch_json("/v1/global-metrics/quotes/latest?convert=USD")
        if not data or not isinstance(data.get("data"), dict):
            return None
        payload = data["data"]
        return {
            "total_open_interest": self._optional_float(payload.get("total_open_interest")),
            "total_open_interest_reported": self._optional_float(
                payload.get("total_open_interest_reported")
            ),
            "funding_rate_avg": self._optional_float(payload.get("funding_rate_avg")),
            "liquidations_24h": self._optional_float(payload.get("liquidations_24h")),
        }

    def get_network_activity(self, symbol: str) -> float | None:
        """Return BSC transfer log count as a network activity proxy.

        Fail-safe: returns None if the tier2 client is unavailable or the call fails.
        """

        if not self.sentiment_tier2:
            return None
        try:
            from src.config.tokens import get_bsc_token_address

            address = get_bsc_token_address(symbol)
        except Exception:
            return None
        try:
            count = self.sentiment_tier2.log_bsc_transfer_count(address)
            if count is None:
                return None
            return float(count)
        except Exception:
            return None

    def compute_sentiment(self) -> SentimentResult:
        """Compute a clamped soft delta for the regime score."""

        delta = 0.0
        fragility = "NONE"
        fear_greed = self.get_fear_greed()
        derivatives = self.get_derivatives_metrics()
        gas_price = self._fetch_gas_price_gwei()

        fgi_value: int | None = None
        fgi_class: str | None = None
        if fear_greed:
            fgi_value = fear_greed.get("value")
            fgi_class = fear_greed.get("classification")
            if fgi_value is not None and fgi_value > 75:
                delta -= 1.0
                fragility = "EXTREME_GREED"
            elif fgi_value is not None and fgi_value < 20:
                delta += 0.5
                fragility = "EXTREME_FEAR"

        funding: float | None = None
        open_interest: float | None = None
        if derivatives:
            funding = derivatives.get("funding_rate_avg")
            open_interest = derivatives.get("total_open_interest")
            if funding is not None and funding > 0.001:
                delta -= 1.0
                if fragility == "NONE":
                    fragility = "CROWDED_LONG"
            elif funding is not None and funding < -0.0005:
                delta += 0.5
                if fragility == "NONE":
                    fragility = "CROWDED_SHORT"

        gas_avg: float | None = None
        if gas_price is not None and gas_price > 0.3:
            delta -= 0.5
            if fragility == "NONE":
                fragility = "GAS_FOMO"

        return SentimentResult(
            fear_greed_index=fgi_value,
            fear_greed_classification=fgi_class,
            funding_rate_btc=funding,
            open_interest_btc=open_interest,
            gas_price_gwei=gas_price,
            gas_avg_24h_gwei=gas_avg,
            sentiment_delta=max(-2.5, min(1.0, delta)),
            regime_fragility=fragility,
        )

    def get_token_sentiment(self, symbol: str) -> dict[str, Any]:
        """Return per-token sentiment from CMC MCP news and narratives.

        Best-effort: if the CMC MCP client is unavailable or the call fails,
        returns an empty dict (caller should treat as neutral).
        """

        result: dict[str, Any] = {}
        if not self.cmc_mcp_client:
            return result
        normalized = symbol.upper()
        try:
            news_payload = self.cmc_mcp_client.get_crypto_latest_news([normalized])
            narratives_payload = self.cmc_mcp_client.get_trending_crypto_narratives([normalized])
        except Exception:
            return result

        news_items = self._extract_items(news_payload)
        narratives_items = self._extract_items(narratives_payload)

        # Simple keyword-based sentiment from news titles
        news_bearish = 0
        news_bullish = 0
        for item in news_items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("title") or item.get("headline") or "").lower()
            if not text:
                continue
            for word in ("crash", "dump", "bearish", "sell", "fall", "decline", "drop", "plunge"):
                if word in text:
                    news_bearish += 1
            for word in ("bullish", "pump", "breakout", "rally", "surge", "moon", "buy"):
                if word in text:
                    news_bullish += 1

        # Simple narrative sentiment
        kol_bullish = 0
        kol_bearish = 0
        for item in narratives_items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("title") or item.get("narrative") or item.get("description") or "").lower()
            if not text:
                continue
            for word in ("bullish", "pump", "breakout", "rally", "surge", "moon", "long"):
                if word in text:
                    kol_bullish += 1
            for word in ("bearish", "dump", "crash", "short", "correction", "fall"):
                if word in text:
                    kol_bearish += 1

        result["news_bearish_last_4h"] = news_bearish > news_bullish and news_bearish > 0
        result["kol_bullish"] = kol_bullish > kol_bearish and kol_bullish > 0
        result["funding_neutral"] = self._is_funding_neutral()
        return result

    def _is_funding_neutral(self) -> bool:
        """Return True if the current funding rate is within a neutral band."""

        derivatives = self.get_derivatives_metrics()
        if not derivatives:
            return True
        funding = derivatives.get("funding_rate_avg")
        if funding is None:
            return True
        return -0.0005 <= funding <= 0.001

    @staticmethod
    def _extract_items(payload: dict[str, Any]) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        for key in ("data", "items", "results", "news", "narratives"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return list(value.values())
        return []

    @staticmethod
    def _first_record(value: object) -> dict | None:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        if isinstance(value, dict):
            return value
        return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
