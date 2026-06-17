"""Tests for trained-model shadow predictor wiring."""

from __future__ import annotations

import json

from src.config.settings import Settings
from src.research.shadow_decisions import ShadowDecisionsLogger
from src.strategy.jump_model_detector import JumpModelDetector, JumpModelResult
from src.strategy.model_predictor import ModelPredictor
from src.strategy.regime_detector import MarketRegime, RegimeResult
from src.strategy.volatility import PriceCache


class _BullModel:
    def detect(self, features: dict) -> JumpModelResult:
        return JumpModelResult("bull", 0.91, 91, {key: float(value) for key, value in features.items()})

    last_features: dict | None = None


class _EchoModel:
    """Captures the feature dict it was scored with for assertions."""

    def __init__(self) -> None:
        self.last_features: dict | None = None

    def detect(self, features: dict) -> JumpModelResult:
        self.last_features = dict(features)
        return JumpModelResult("bull", 0.8, 80, {k: float(v) for k, v in features.items()})


class _Candidate:
    def __init__(self) -> None:
        self.symbol = "CAKE"
        self.factor_scores = {"volume_breakout": True, "rsi_in_range": False}
        self.entry_score = 55.0
        self.true_factor_count = 3


def _regime() -> RegimeResult:
    return RegimeResult(MarketRegime.TRENDING_UP, 4.0, [], 1.0, 4, 0.01, 0.0, "NONE")


_SNAPSHOT = {"BNB": {"percent_change_1h": 0.01, "percent_change_6h": 0.02, "percent_change_24h": 0.03}}


def test_model_predictor_fails_closed_when_artifact_missing(tmp_path: object) -> None:
    predictor = ModelPredictor(str(tmp_path / "missing.pkl"))  # type: ignore[operator]
    result = predictor.detect({"momentum_10": 1})

    assert result.state == "bear"
    assert result.confidence == 0.0
    assert result.source == "SHADOW"


def test_shadow_logs_trained_model_only_with_candidate_and_flag(tmp_path: object) -> None:
    path = tmp_path / "decision_shadow.jsonl"  # type: ignore[operator]
    logger = ShadowDecisionsLogger(
        JumpModelDetector(PriceCache()),
        model_predictor=_BullModel(),
        settings=Settings(enable_model_shadow=True),
        decision_log_path=str(path),
    )

    assert logger.log_all_variants(1, _SNAPSHOT, _regime(), candidate=_Candidate()) is None
    variants = [json.loads(line)["variant"] for line in path.read_text(encoding="utf-8").splitlines()]

    assert variants == ["jump_inspired", "trained_model"]


def test_shadow_skips_trained_model_when_no_candidate(tmp_path: object) -> None:
    path = tmp_path / "decision_shadow.jsonl"  # type: ignore[operator]
    logger = ShadowDecisionsLogger(
        JumpModelDetector(PriceCache()),
        model_predictor=_BullModel(),
        settings=Settings(enable_model_shadow=True),
        decision_log_path=str(path),
    )

    logger.log_all_variants(1, _SNAPSHOT, _regime(), candidate=None)
    variants = [json.loads(line)["variant"] for line in path.read_text(encoding="utf-8").splitlines()]

    assert variants == ["jump_inspired"]


def test_shadow_scores_candidate_with_canonical_feature_contract(tmp_path: object) -> None:
    """The model must be scored on factor_*/entry_score features, not BNB proxies."""

    from src.research.feature_contract import entry_feature_vector

    path = tmp_path / "decision_shadow.jsonl"  # type: ignore[operator]
    model = _EchoModel()
    logger = ShadowDecisionsLogger(
        JumpModelDetector(PriceCache()),
        model_predictor=model,
        settings=Settings(enable_model_shadow=True),
        decision_log_path=str(path),
    )

    candidate = _Candidate()
    logger.log_all_variants(1, _SNAPSHOT, _regime(), candidate=candidate)

    expected = entry_feature_vector(
        factor_scores=candidate.factor_scores,
        entry_score=candidate.entry_score,
        true_factor_count=candidate.true_factor_count,
    )
    assert model.last_features == expected
    assert "factor_volume_breakout" in model.last_features
    # The old (buggy) BNB-proxy keys must NOT be what the model sees.
    assert "momentum_10" not in model.last_features
