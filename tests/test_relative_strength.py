"""Tests for relative strength scoring."""

from __future__ import annotations

from src.strategy.relative_strength import calculate_relative_strength


def test_token_underperforming_bnb_scores_lower() -> None:
    snapshot = {"BNB": {"percent_change_6h": 0.08}, "CAKE": {"percent_change_6h": 0.05}, "AAVE": {"percent_change_6h": 0.05}}
    result = calculate_relative_strength("CAKE", snapshot)
    assert result.score < 0


def test_token_outperforming_both_scores_high() -> None:
    snapshot = {"BNB": {"percent_change_6h": 0.03}, "CAKE": {"percent_change_6h": 0.09}, "AAVE": {"percent_change_6h": 0.01}}
    result = calculate_relative_strength("CAKE", snapshot)
    assert result.score > 0.5


def test_empty_universe_does_not_crash() -> None:
    snapshot = {"BNB": {"percent_change_6h": 0.03}}
    result = calculate_relative_strength("CAKE", snapshot)
    assert result.score == 0.0


def test_score_bounded() -> None:
    snapshot = {"BNB": {"percent_change_6h": -1.0}, "CAKE": {"percent_change_6h": 1.0}, "AAVE": {"percent_change_6h": -1.0}}
    result = calculate_relative_strength("CAKE", snapshot)
    assert -1.0 <= result.score <= 1.0


def test_missing_bnb_returns_neutral_edge() -> None:
    result = calculate_relative_strength("CAKE", {"CAKE": {"percent_change_6h": 0.1}})
    assert result.score == 0.0


def test_stables_are_excluded_from_universe() -> None:
    snapshot = {"BNB": {"percent_change_6h": 0.0}, "CAKE": {"percent_change_6h": 0.02}, "USDC": {"percent_change_6h": -1.0}, "AAVE": {"percent_change_6h": 0.0}}
    result = calculate_relative_strength("CAKE", snapshot)
    assert result.vs_universe_avg == 0.02
