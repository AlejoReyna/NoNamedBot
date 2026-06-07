"""Tests for shadow-only modules."""

from __future__ import annotations

import json

from src.research.shadow_decisions import ShadowDecisionsLogger, assert_shadow_isolation
from src.strategy.bb_squeeze import detect_bb_squeeze
from src.strategy.jump_model_detector import JumpModelDetector
from src.strategy.regime_detector import MarketRegime, RegimeResult
from src.strategy.sentiment_tier2 import SentimentTier2
from src.strategy.volatility import PriceCache


def test_persistence_keeps_previous_state_when_confidence_low() -> None:
    detector = JumpModelDetector(PriceCache())
    detector._previous_state = "bull"
    detector._previous_confidence = 0.8
    result = detector.detect({"momentum_10": -0.01, "downside_deviation_10": 0.01, "sortino_20_proxy": 1})
    assert result.state == "bull"


def test_state_changes_when_confidence_high() -> None:
    detector = JumpModelDetector(PriceCache())
    detector._previous_state = "bull"
    result = detector.detect({"momentum_10": -0.01, "downside_deviation_10": 0.10})
    assert result.state == "bear"


def test_source_is_always_shadow() -> None:
    result = JumpModelDetector(PriceCache()).detect({"momentum_10": 0.1, "sortino_20_proxy": 1, "sortino_60_proxy": 1})
    assert result.source == "SHADOW"


def test_squeeze_detected_when_bands_narrow() -> None:
    closes = [100.0] * 50
    atrs = [1.0] * 50
    result = detect_bb_squeeze(closes, atrs)
    assert result.detected is True


def test_squeeze_not_detected_when_volatile() -> None:
    closes = list(range(100, 150))
    atrs = [5.0] * 50
    result = detect_bb_squeeze(closes, atrs)
    assert result.detected is False


def test_squeeze_insufficient_data_edge() -> None:
    result = detect_bb_squeeze([100.0], [1.0])
    assert result.detected is False


def test_sentiment_tier2_keyword_count_logs_shadow(tmp_path: object) -> None:
    path = tmp_path / "sentiment_shadow.jsonl"  # type: ignore[operator]
    result = SentimentTier2(log_path=str(path)).log_keyword_count("bull breakout but crash risk")
    assert result is not None
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["path"] == "shadow"


def test_sentiment_tier2_keyword_count_returns_none_without_keywords(tmp_path: object) -> None:
    path = tmp_path / "sentiment_shadow.jsonl"  # type: ignore[operator]
    assert SentimentTier2(log_path=str(path)).log_keyword_count("plain text") is None


def test_sentiment_tier2_alternative_me_happy_path(tmp_path: object) -> None:
    path = tmp_path / "sentiment_shadow.jsonl"  # type: ignore[operator]
    tier2 = SentimentTier2(log_path=str(path))
    tier2._fetch_json = lambda _url: {"data": [{"value": "81", "value_classification": "Extreme Greed"}]}  # type: ignore[method-assign]
    result = tier2.log_alternative_me_fng()
    assert result == {"value": 81.0, "classification": "Extreme Greed"}


def test_sentiment_tier2_alternative_me_bad_payload_edge(tmp_path: object) -> None:
    tier2 = SentimentTier2(log_path=str(tmp_path / "sentiment_shadow.jsonl"))  # type: ignore[operator]
    tier2._fetch_json = lambda _url: {"data": []}  # type: ignore[method-assign]
    assert tier2.log_alternative_me_fng() is None


def test_sentiment_tier2_transfer_count_happy_path(tmp_path: object) -> None:
    path = tmp_path / "sentiment_shadow.jsonl"  # type: ignore[operator]
    tier2 = SentimentTier2(bsc_rpc_url="https://rpc.example", log_path=str(path), min_rpc_interval_seconds=0)
    tier2._post_json = lambda _url, _payload: {"result": [{"x": 1}, {"x": 2}]}  # type: ignore[method-assign]
    assert tier2.log_bsc_transfer_count("0x0000000000000000000000000000000000000001") == 2


def test_sentiment_tier2_transfer_count_rate_limited_edge(tmp_path: object) -> None:
    tier2 = SentimentTier2(bsc_rpc_url="https://rpc.example", log_path=str(tmp_path / "s.jsonl"))
    token = "0x0000000000000000000000000000000000000001"
    tier2._last_transfer_call_by_token[token.lower()] = 999999999999.0
    assert tier2.log_bsc_transfer_count(token) is None


def test_shadow_logger_does_not_modify_live_state(tmp_path: object) -> None:
    path = tmp_path / "decision_shadow.jsonl"  # type: ignore[operator]
    logger = ShadowDecisionsLogger(JumpModelDetector(PriceCache()), decision_log_path=str(path))
    snapshot = {"BNB": {"percent_change_1h": 0.01, "percent_change_6h": 0.02, "percent_change_24h": 0.03}}
    regime = RegimeResult(MarketRegime.TRENDING_UP, 4.0, [], 1.0, 4, 0.01, 0.0, "NONE")
    before = dict(snapshot["BNB"])
    assert logger.log_all_variants(1, snapshot, regime) is None
    assert snapshot["BNB"] == before
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["path"] == "shadow"


def test_assert_shadow_isolation() -> None:
    assert assert_shadow_isolation() is True
