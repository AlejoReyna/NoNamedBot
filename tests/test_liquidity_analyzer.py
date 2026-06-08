"""Tests for quote-derived liquidity analysis."""

from __future__ import annotations

from src.execution.liquidity_analyzer import analyze_liquidity


def test_reject_when_slippage_exceeds_max() -> None:
    result = analyze_liquidity("CAKE", 100.0, 0.002, 0.015, 0.01)
    assert result.recommendation == "REJECT"


def test_reduce_size_when_convex() -> None:
    result = analyze_liquidity("CAKE", 100.0, 0.001, 0.008, 0.01)
    assert result.recommendation == "REDUCE_SIZE"
    assert result.slippage_curve_convex is True


def test_proceed_when_linear_and_under_cap() -> None:
    result = analyze_liquidity("CAKE", 100.0, 0.001, 0.002, 0.01)
    assert result.recommendation == "PROCEED"


def test_reject_when_normal_quote_missing_edge() -> None:
    result = analyze_liquidity("CAKE", 100.0, 0.001, None, 0.01)
    assert result.recommendation == "REJECT"


def test_reject_when_position_is_zero_edge() -> None:
    result = analyze_liquidity("CAKE", 0.0, 0.001, 0.002, 0.01)
    assert result.recommendation == "REJECT"


def test_negative_slippage_fails_closed_edge() -> None:
    result = analyze_liquidity("CAKE", 100.0, 0.001, -0.002, 0.01)
    assert result.recommendation == "REJECT"
