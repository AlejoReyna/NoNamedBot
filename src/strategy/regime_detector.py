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

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from src.config.settings import Settings
from src.strategy.sentiment_tier1 import SentimentTier1
from src.strategy.volatility import PriceCache

LOGGER = logging.getLogger(__name__)


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
        regime_predictor: Any | None = None,
    ) -> None:
        self.price_cache = price_cache
        self.sentiment = sentiment
        self.settings = settings
        self.regime_predictor = regime_predictor

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
            regime = MarketRegime.TRENDING_UP
            base_multiplier = 1.0
            min_factors = 4
            max_slippage = self.settings.max_slippage_pct
        elif score >= 1.0:
            regime = MarketRegime.RANGING
            base_multiplier = 0.5
            min_factors = 5
            max_slippage = min(self.settings.max_slippage_pct, 0.0075)
        else:
            regime = MarketRegime.RISK_OFF
            base_multiplier = 0.1
            min_factors = 5
            max_slippage = min(self.settings.max_slippage_pct, 0.005)

        # Optional ML regime model modulation
        if self.regime_predictor is not None:
            try:
                bnb_ohlcv = self._price_cache_to_ohlcv_df("BNB")
                if bnb_ohlcv is not None and not bnb_ohlcv.empty:
                    prediction = self.regime_predictor.predict(bnb_ohlcv, {})
                    confidence = prediction.confidence
                    reasons.append(f"regime_model_confidence={confidence:.2f}")
                    if confidence > 0.65:
                        base_multiplier = 1.0
                    elif confidence >= 0.55:
                        base_multiplier = min(base_multiplier, 0.6)
                    else:
                        base_multiplier = min(base_multiplier, 0.2)
            except Exception as exc:
                LOGGER.warning("RegimePredictor modulation failed: %s", exc)

        return RegimeResult(
            regime=regime,
            score=score,
            reasons=reasons,
            position_multiplier=base_multiplier,
            min_entry_factors=min_factors,
            max_slippage_pct=max_slippage,
            sentiment_delta=sentiment_delta,
            sentiment_fragility=sentiment_fragility,
        )

    def _price_cache_to_ohlcv_df(self, symbol: str) -> pd.DataFrame | None:
        """Convert PriceCache OHLCV deque to a pandas DataFrame for RegimePredictor."""
        points = list(self.price_cache._data.get(symbol.upper(), ()))
        if not points:
            return None
        return pd.DataFrame([
            {
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "close": p.close,
                "volume": p.volume,
                "timestamp": p.timestamp,
            }
            for p in points
        ])

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
