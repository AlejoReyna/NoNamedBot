"""Integration tests for ML wiring in main loop."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

from src.config.settings import Settings
from src.main import _build_ml_bundle, _evaluate_universe_v25
from src.ml.types import MLContext
from src.strategy.guardrails import RiskDecision, RiskState
from src.strategy.regime_detector import MarketRegime, RegimeResult

_evaluator = importlib.import_module("src.strategy.6falgorithm.evaluator")
evaluate_universe_breakout = _evaluator.evaluate_universe_breakout


def _regime() -> RegimeResult:
    return RegimeResult(
        regime=MarketRegime.RANGING,
        score=1.0,
        reasons=[],
        position_multiplier=1.0,
        min_entry_factors=4,
        max_slippage_pct=0.01,
        sentiment_delta=0.0,
        sentiment_fragility="NONE",
    )


def _risk() -> RiskDecision:
    return RiskDecision(
        state=RiskState.NORMAL,
        allow_new_entries=True,
        position_multiplier=1.0,
        max_slippage_pct=0.01,
        max_daily_trades=3,
        base_risk_per_trade_pct=0.0035,
        reasons=[],
    )


def test_evaluate_universe_v25_forwards_ml_bundle() -> None:
    settings = Settings(paper_trade=True, strategy_mode="breakout")
    ml_bundle = MagicMock()
    ml_bundle.build_contexts.return_value = {}

    snapshot = {
        "CAKE": {
            "symbol": "CAKE",
            "price": 2.0,
            "volume_24h": 1_000_000.0,
            "market_cap": 10_000_000.0,
        }
    }

    with patch("src.main.scoring") as scoring_mock:
        scoring_mock.evaluate_universe = MagicMock(return_value=None)
        _evaluate_universe_v25(
            snapshot,
            10_000.0,
            _regime(),
            _risk(),
            settings,
            ml_bundle=ml_bundle,
        )
        kwargs = scoring_mock.evaluate_universe.call_args.kwargs
        assert kwargs.get("ml_bundle") is ml_bundle


def test_build_ml_bundle_returns_none_when_disabled() -> None:
    settings = Settings(paper_trade=True, ml_enabled=False)
    assert _build_ml_bundle(settings) is None


def test_build_ml_bundle_fails_closed_when_model_missing() -> None:
    settings = Settings(
        paper_trade=True,
        ml_enabled=True,
        ml_model_path="models/does_not_exist.pkl",
    )
    bundle = _build_ml_bundle(settings)
    assert bundle is None


def test_evaluate_universe_breakout_attaches_ml_audit() -> None:
    settings = Settings(
        paper_trade=True,
        strategy_mode="breakout",
        breakout_entry_score_min=45.0,
        max_position_pct=0.05,
    )
    ml_bundle = MagicMock()
    ml_bundle.is_ranking_active = False
    ml_bundle.validation_auc = 0.55
    ml_bundle.settings.ml_shadow_mode = True
    ml_context = MLContext("CAKE", "momentum", 0.72, 1.0, 0.72, {})
    ml_bundle.build_contexts.return_value = {"CAKE": ml_context}

    snapshot = {
        "CAKE": {
            "symbol": "CAKE",
            "price": 2.0,
            "volume_24h": 50_000_000.0,
            "market_cap": 1_000_000_000.0,
            "volume_1h": 5_000_000.0,
            "rolling_24h_hourly_volume_avg": 1_000_000.0,
            "high_6h": 1.95,
            "percent_change_1h": 0.02,
            "percent_change_24h": 0.05,
            "bnb_1h_trend_pct": 0.01,
            "token_percent_change_1h": 0.02,
            "token_percent_change_24h": 0.05,
            "rsi": 55.0,
            "funding_rate": 0.0001,
            "open_interest_change_pct": 0.5,
            "estimated_slippage_pct": 0.005,
        }
    }

    candidate = evaluate_universe_breakout(
        snapshot,
        10_000.0,
        _regime(),
        _risk(),
        settings=settings,
        ml_bundle=ml_bundle,
    )
    assert candidate is not None
    assert candidate.ml_audit is not None
    assert candidate.ml_audit["ml_enabled"] is True
    assert candidate.ml_audit["ml_active"] is False
    assert candidate.ml_audit["ml_shadow_mode"] is True
    assert candidate.ml_audit["ml_validation_auc"] == 0.55
    assert candidate.ml_audit["ml_regime"] == "momentum"
    assert candidate.ml_audit["ml_confidence"] == 0.72


def test_ml_ranking_selects_highest_confidence_passer() -> None:
    settings = Settings(
        paper_trade=True,
        strategy_mode="breakout",
        breakout_entry_score_min=45.0,
        max_position_pct=0.05,
    )
    ml_bundle = MagicMock()
    ml_bundle.is_ranking_active = True
    ml_bundle.validation_auc = 0.70
    ml_bundle.settings.ml_shadow_mode = False
    ml_bundle.build_contexts.return_value = {
        "CAKE": MLContext("CAKE", "momentum", 0.61, 1.0, 0.61, {}),
        "LINK": MLContext("LINK", "momentum", 0.82, 1.0, 0.82, {}),
    }

    base = {
        "volume_24h": 50_000_000.0,
        "market_cap": 1_000_000_000.0,
        "volume_1h": 5_000_000.0,
        "rolling_24h_hourly_volume_avg": 1_000_000.0,
        "percent_change_1h": 0.02,
        "percent_change_24h": 0.05,
        "bnb_1h_trend_pct": 0.01,
        "token_percent_change_1h": 0.02,
        "token_percent_change_24h": 0.05,
        "rsi": 55.0,
        "funding_rate": 0.0001,
        "open_interest_change_pct": 0.5,
        "estimated_slippage_pct": 0.005,
    }
    # Use price just above the 6h high for both so each passes the core gate.
    snapshot = {
        "CAKE": {"symbol": "CAKE", "price": 2.01, "high_6h": 2.0, **base},
        "LINK": {"symbol": "LINK", "price": 15.08, "high_6h": 15.0, **base},
    }

    candidate = evaluate_universe_breakout(
        snapshot,
        10_000.0,
        _regime(),
        _risk(),
        settings=settings,
        ml_bundle=ml_bundle,
    )
    assert candidate is not None
    assert candidate.symbol == "LINK"
    assert candidate.ml_audit is not None
    assert candidate.ml_audit["ml_active"] is True
    ranking_audit = candidate.ml_audit.get("ml_ranking_audit")
    assert ranking_audit is not None
    assert ranking_audit["selected"] == "LINK"
    assert set(ranking_audit["candidates"]) == {"CAKE", "LINK"}


def test_ml_bundle_context_build_failure_falls_back_to_rule_ranking() -> None:
    settings = Settings(
        paper_trade=True,
        strategy_mode="breakout",
        breakout_entry_score_min=45.0,
        max_position_pct=0.05,
    )
    ml_bundle = MagicMock()
    ml_bundle.is_ranking_active = False
    ml_bundle.validation_auc = 0.55
    ml_bundle.settings.ml_shadow_mode = True
    ml_bundle.refresh_ohlcv_if_stale.side_effect = RuntimeError("network down")

    snapshot = {
        "CAKE": {
            "symbol": "CAKE",
            "price": 2.0,
            "volume_24h": 50_000_000.0,
            "market_cap": 1_000_000_000.0,
            "volume_1h": 5_000_000.0,
            "rolling_24h_hourly_volume_avg": 1_000_000.0,
            "high_6h": 1.95,
            "percent_change_1h": 0.02,
            "percent_change_24h": 0.05,
            "bnb_1h_trend_pct": 0.01,
            "token_percent_change_1h": 0.02,
            "token_percent_change_24h": 0.05,
            "rsi": 55.0,
            "funding_rate": 0.0001,
            "open_interest_change_pct": 0.5,
            "estimated_slippage_pct": 0.005,
        }
    }

    candidate = evaluate_universe_breakout(
        snapshot,
        10_000.0,
        _regime(),
        _risk(),
        settings=settings,
        ml_bundle=ml_bundle,
    )
    assert candidate is not None
    assert candidate.symbol == "CAKE"
    assert candidate.ml_audit is not None
    assert candidate.ml_audit["ml_enabled"] is True
    assert candidate.ml_audit["ml_active"] is False
