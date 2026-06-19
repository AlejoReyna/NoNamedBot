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

# Consecutive cycles a worse signal must persist before a regime downgrade commits.
# Upgrades are always immediate. Set to 1 to disable hysteresis.
_DOWNGRADE_HOLD_CYCLES: int = 3


class MarketRegime(Enum):
    """Coarse deterministic market regimes."""

    TRENDING_UP = "trending_up"
    RANGING = "ranging"
    RISK_OFF = "risk_off"


# Regime ordering for hysteresis comparisons (higher = better market condition).
_REGIME_ORDER: dict[str, int] = {
    MarketRegime.TRENDING_UP.value: 2,
    MarketRegime.RANGING.value: 1,
    MarketRegime.RISK_OFF.value: 0,
}


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
    """Rules-based detector with BTC as the primary macro signal.

    Scoring (range -2.5 to +6.0):
        BTC 1h % change > 0          → +1.0
        BTC 6h % change > 0          → +1.0
        BTC 24h % change > 0         → +1.0
        BSC universe breadth ≥ 60%   → +1.0 (< 35% → -1.0)
        BTC price above EMA-288      → +1.0 (below → -1.0)
        Sentiment delta              → variable (typically -2.5 to +1.0)

    Thresholds: TRENDING_UP ≥ 3.0 · RANGING ≥ 1.0 · RISK_OFF < 1.0

    Hysteresis: a downgrade requires _DOWNGRADE_HOLD_CYCLES consecutive
    cycles where the raw score would produce a lower regime.  Upgrades
    commit immediately so the bot never misses a risk-on window.
    """

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
        # Hysteresis state — intentionally not reset between cycles
        self._last_regime: MarketRegime | None = None
        self._downgrade_hold: int = 0

    def detect(self, snapshot: dict[str, dict[str, Any]]) -> RegimeResult:
        """Classify the market from normalized decimal percentage inputs."""

        btc = snapshot.get("BTC", {})
        reasons: list[str] = []
        score = 0.0

        # ── BTC price-action factors (3 pts max) ────────────────────────────
        btc_1h = self._number(btc.get("percent_change_1h"), 0.0)
        btc_6h = self._number(btc.get("percent_change_6h"), 0.0)
        btc_24h = self._number(btc.get("percent_change_24h"), 0.0)
        if btc_1h > 0:
            score += 1.0
            reasons.append("btc_1h_positive")
        if btc_6h > 0:
            score += 1.0
            reasons.append("btc_6h_positive")
        if btc_24h > 0:
            score += 1.0
            reasons.append("btc_24h_positive")

        # ── BSC universe breadth (±1 pt) ────────────────────────────────────
        changes_6h = self._universe_changes(snapshot)
        if changes_6h:
            breadth = sum(1 for value in changes_6h if value > 0) / len(changes_6h)
            if breadth >= 0.60:
                score += 1.0
                reasons.append("universe_breadth_strong")
            elif breadth < 0.35:
                score -= 1.0
                reasons.append("universe_breadth_weak")

        # ── BTC vs EMA-288 (±1 pt, requires 24 h of 5-min data) ────────────
        btc_price = self._optional_number(btc.get("price"))
        btc_ema_288 = self.price_cache.get_ema("BTC", periods=288)
        if btc_price is not None and btc_ema_288 is not None:
            if btc_price > btc_ema_288:
                score += 1.0
                reasons.append("btc_above_ema288")
            else:
                score -= 1.0
                reasons.append("btc_below_ema288")

        # ── Sentiment delta (variable, −2.5 to +1.0 typical) ────────────────
        sentiment_result = self.sentiment.compute_sentiment()
        score += sentiment_result.sentiment_delta
        if sentiment_result.regime_fragility != "NONE":
            reasons.append(f"sentiment_{sentiment_result.regime_fragility}")

        # ── Volatility breaker (annotation only, does not alter score) ───────
        atr_1h = self.price_cache.get_atr_pct("BTC", periods=14)
        atr_24h = self.price_cache.get_atr_pct("BTC", periods=288)
        if atr_1h is not None and atr_24h is not None:
            if atr_1h > 3 * atr_24h and btc_1h < -0.015:
                reasons.append("volatility_breaker_reported")

        candidate = self._result(
            score, reasons,
            sentiment_result.sentiment_delta,
            sentiment_result.regime_fragility,
        )

        # ── Hysteresis ────────────────────────────────────────────────────────
        # Upgrades commit immediately.  Downgrades require _DOWNGRADE_HOLD_CYCLES
        # consecutive cycles of a lower raw score before the new regime applies.
        if (
            self._last_regime is not None
            and _REGIME_ORDER[candidate.regime.value] < _REGIME_ORDER[self._last_regime.value]
        ):
            self._downgrade_hold += 1
            if self._downgrade_hold < _DOWNGRADE_HOLD_CYCLES:
                hold_reasons = list(reasons) + [
                    f"hysteresis_hold_{self._downgrade_hold}of{_DOWNGRADE_HOLD_CYCLES}"
                ]
                return self._build_result(
                    self._last_regime, score, hold_reasons,
                    sentiment_result.sentiment_delta,
                    sentiment_result.regime_fragility,
                )
            # Threshold reached — commit the downgrade and reset counter.
            self._downgrade_hold = 0
        else:
            self._downgrade_hold = 0

        self._last_regime = candidate.regime
        return candidate

    def _result(
        self,
        score: float,
        reasons: list[str],
        sentiment_delta: float,
        sentiment_fragility: str,
    ) -> RegimeResult:
        """Derive regime + params from score, then optionally apply ML modulation."""
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
                btc_ohlcv = self._price_cache_to_ohlcv_df("BTC")
                if btc_ohlcv is not None and not btc_ohlcv.empty:
                    prediction = self.regime_predictor.predict(btc_ohlcv, {})
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

    def _build_result(
        self,
        regime: MarketRegime,
        score: float,
        reasons: list[str],
        sentiment_delta: float,
        sentiment_fragility: str,
    ) -> RegimeResult:
        """Build a RegimeResult for an explicit regime without ML modulation.

        Used by the hysteresis hold path to preserve the last regime's
        execution parameters while surfacing the current raw score.
        """
        if regime == MarketRegime.TRENDING_UP:
            base_multiplier = 1.0
            min_factors = 4
            max_slippage = self.settings.max_slippage_pct
        elif regime == MarketRegime.RANGING:
            base_multiplier = 0.5
            min_factors = 5
            max_slippage = min(self.settings.max_slippage_pct, 0.0075)
        else:
            base_multiplier = 0.1
            min_factors = 5
            max_slippage = min(self.settings.max_slippage_pct, 0.005)

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
        excluded = {"USDT", "USDC", "BUSD", "BNB", "BTC"}
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
