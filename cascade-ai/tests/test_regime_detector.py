"""Tests for deterministic regime detection."""

from __future__ import annotations

from src.config.settings import Settings
from src.strategy.regime_detector import MarketRegime, RegimeDetector


class FakeCache:
    def __init__(self, ema: float | None = 100.0, atr_1h: float | None = None, atr_24h: float | None = None) -> None:
        self.ema = ema
        self.atr_1h = atr_1h
        self.atr_24h = atr_24h

    def get_ema(self, _symbol: str, periods: int) -> float | None:
        return self.ema if periods == 288 else None

    def get_atr_pct(self, _symbol: str, periods: int) -> float | None:
        if periods == 14:
            return self.atr_1h
        if periods == 288:
            return self.atr_24h
        return None


class FakeSentiment:
    def __init__(self, delta: float = 0.0, fragility: str = "NONE") -> None:
        self.delta = delta
        self.fragility = fragility

    def compute_sentiment(self) -> object:
        return type(
            "Sentiment",
            (),
            {"sentiment_delta": self.delta, "regime_fragility": self.fragility},
        )()


def _detector(cache: FakeCache | None = None, sentiment: FakeSentiment | None = None) -> RegimeDetector:
    return RegimeDetector(cache or FakeCache(), sentiment or FakeSentiment(), Settings())  # type: ignore[arg-type]


def _snapshot(
    bnb_1h: float = 0.01,
    bnb_6h: float = 0.01,
    bnb_24h: float = 0.01,
    positives: int = 3,
    negatives: int = 1,
    bnb_price: float = 110.0,
) -> dict[str, dict[str, float]]:
    data = {
        "BNB": {
            "price": bnb_price,
            "percent_change_1h": bnb_1h,
            "percent_change_6h": bnb_6h,
            "percent_change_24h": bnb_24h,
        }
    }
    for index in range(positives):
        data[f"P{index}"] = {"percent_change_6h": 0.01}
    for index in range(negatives):
        data[f"N{index}"] = {"percent_change_6h": -0.01}
    return data


def test_regime_trending_up_when_bnb_positive_all_timeframes() -> None:
    result = _detector().detect(_snapshot(positives=3, negatives=1))
    assert result.regime == MarketRegime.TRENDING_UP
    assert result.position_multiplier == 1.0
    assert result.min_entry_factors == 4


def test_regime_ranging_when_mixed_signals() -> None:
    result = _detector(FakeCache(ema=None)).detect(_snapshot(bnb_6h=-0.005, positives=2, negatives=2))
    assert result.regime == MarketRegime.RANGING


def test_regime_risk_off_when_bnb_negative_all_timeframes() -> None:
    result = _detector(FakeCache(ema=100.0)).detect(
        _snapshot(bnb_1h=-0.02, bnb_6h=-0.05, bnb_24h=-0.08, positives=0, negatives=4, bnb_price=90)
    )
    assert result.regime == MarketRegime.RISK_OFF
    assert result.position_multiplier == 0.1


def test_regime_uses_breadth_not_only_bnb() -> None:
    result = _detector().detect(_snapshot(bnb_6h=0.01, positives=1, negatives=4))
    assert result.score < 4.0


def test_sentiment_extreme_greed_reduces_score() -> None:
    result = _detector(sentiment=FakeSentiment(delta=-1.0, fragility="EXTREME_GREED")).detect(
        _snapshot(positives=3, negatives=1)
    )
    assert result.score == 4.0
    assert result.sentiment_fragility == "EXTREME_GREED"
    assert result.regime == MarketRegime.TRENDING_UP


def test_sentiment_crowded_long_can_flip_to_ranging() -> None:
    result = _detector(FakeCache(ema=None), FakeSentiment(delta=-1.0, fragility="CROWDED_LONG")).detect(
        _snapshot(positives=2, negatives=2)
    )
    assert result.regime == MarketRegime.RANGING
    assert result.sentiment_fragility == "CROWDED_LONG"


def test_sentiment_neutral_does_not_change_score() -> None:
    result = _detector().detect(_snapshot(positives=3, negatives=1))
    assert result.sentiment_delta == 0.0
    assert result.score == 5.0


def test_regime_normalizes_percentage_units() -> None:
    result = _detector(FakeCache(ema=None)).detect(_snapshot(bnb_1h=0.015, positives=0, negatives=0))
    assert result.score >= 1.0


def test_volatility_breaker_reported_not_hard() -> None:
    result = _detector(FakeCache(ema=100.0, atr_1h=0.09, atr_24h=0.02)).detect(
        _snapshot(bnb_1h=-0.02, bnb_6h=-0.05, bnb_24h=-0.08, positives=0, negatives=4, bnb_price=90)
    )
    assert "volatility_breaker_reported" in result.reasons
    assert result.regime == MarketRegime.RISK_OFF
