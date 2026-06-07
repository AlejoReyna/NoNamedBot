"""Deterministic multi-factor market regime detector.

Example:
    detector = RegimeDetector(price_cache, sentiment, settings)
    result = detector.detect(snapshot)

Interface contract:
    Imports: strategy volatility/sentiment and settings types.
    Exports: MarketRegime, RegimeResult, RegimeDetector.
    Does not execute trades or mutate live state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.config.settings import Settings
from src.strategy.sentiment_tier1 import SentimentTier1
from src.strategy.volatility import PriceCache


class MarketRegime(Enum):
    """Coarse deterministic market regimes."""

    TRENDING_UP = "trending_up"
    RANGING = "ranging"
    RISK_OFF = "risk_off"


@dataclass(frozen=True)
class RegimeResult:
    """Regime classification plus execution/risk parameters."""

    regime: MarketRegime
    score: float
    reasons: list[str]
    position_multiplier: float
    min_entry_factors: int
    max_slippage_pct: float
    sentiment_delta: float
    sentiment_fragility: str


class RegimeDetector:
    """Rules-based detector with sentiment as a soft score modifier only."""

    def __init__(
        self,
        price_cache: PriceCache,
        sentiment: SentimentTier1,
        settings: Settings,
    ) -> None:
        self.price_cache = price_cache
        self.sentiment = sentiment
        self.settings = settings

    def detect(self, snapshot: dict[str, dict[str, Any]]) -> RegimeResult:
        """Classify the market from normalized decimal percentage inputs."""

        bnb = snapshot.get("BNB", {})
        reasons: list[str] = []
        score = 0.0

        bnb_1h = self._number(bnb.get("percent_change_1h"), 0.0)
        bnb_6h = self._number(bnb.get("percent_change_6h"), 0.0)
        bnb_24h = self._number(bnb.get("percent_change_24h"), 0.0)
        if bnb_1h > 0:
            score += 1.0
            reasons.append("bnb_1h_positive")
        if bnb_6h > 0:
            score += 1.0
            reasons.append("bnb_6h_positive")
        if bnb_24h > 0:
            score += 1.0
            reasons.append("bnb_24h_positive")

        changes_6h = self._universe_changes(snapshot)
        if changes_6h:
            breadth = sum(1 for value in changes_6h if value > 0) / len(changes_6h)
            if breadth >= 0.60:
                score += 1.0
                reasons.append("universe_breadth_strong")
            elif breadth < 0.35:
                score -= 1.0
                reasons.append("universe_breadth_weak")

        bnb_price = self._optional_number(bnb.get("price"))
        bnb_ema_288 = self.price_cache.get_ema("BNB", periods=288)
        if bnb_price is not None and bnb_ema_288 is not None:
            if bnb_price > bnb_ema_288:
                score += 1.0
                reasons.append("bnb_above_ema288")
            else:
                score -= 1.0
                reasons.append("bnb_below_ema288")

        sentiment_result = self.sentiment.compute_sentiment()
        score += sentiment_result.sentiment_delta
        if sentiment_result.regime_fragility != "NONE":
            reasons.append(f"sentiment_{sentiment_result.regime_fragility}")

        atr_1h = self.price_cache.get_atr_pct("BNB", periods=14)
        atr_24h = self.price_cache.get_atr_pct("BNB", periods=288)
        if atr_1h is not None and atr_24h is not None:
            if atr_1h > 3 * atr_24h and bnb_1h < -0.015:
                reasons.append("volatility_breaker_reported")

        return self._result(score, reasons, sentiment_result.sentiment_delta, sentiment_result.regime_fragility)

    def _result(
        self,
        score: float,
        reasons: list[str],
        sentiment_delta: float,
        sentiment_fragility: str,
    ) -> RegimeResult:
        if score >= 3.0:
            return RegimeResult(
                MarketRegime.TRENDING_UP,
                score,
                reasons,
                1.0,
                4,
                self.settings.max_slippage_pct,
                sentiment_delta,
                sentiment_fragility,
            )
        if score >= 1.0:
            return RegimeResult(
                MarketRegime.RANGING,
                score,
                reasons,
                0.5,
                5,
                min(self.settings.max_slippage_pct, 0.0075),
                sentiment_delta,
                sentiment_fragility,
            )
        return RegimeResult(
            MarketRegime.RISK_OFF,
            score,
            reasons,
            0.1,
            5,
            min(self.settings.max_slippage_pct, 0.005),
            sentiment_delta,
            sentiment_fragility,
        )

    @staticmethod
    def _universe_changes(snapshot: dict[str, dict[str, Any]]) -> list[float]:
        excluded = {"USDT", "USDC", "BUSD", "BNB"}
        values: list[float] = []
        for symbol, data in snapshot.items():
            if symbol.upper() in excluded:
                continue
            value = RegimeDetector._optional_number(data.get("percent_change_6h"))
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _optional_number(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: object, default: float) -> float:
        parsed = RegimeDetector._optional_number(value)
        return default if parsed is None else parsed
