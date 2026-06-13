"""Tests for v2 position sizing helpers."""

from __future__ import annotations

from pathlib import Path

from src.config.settings import Settings
from src.strategy.position_manager import PositionManager, calculate_exit_levels, calculate_position_pct
from src.strategy.regime_detector import MarketRegime


def test_position_size_decreases_as_atr_increases() -> None:
    low_vol = calculate_position_pct(1000, 0.02, 1.0, 1.0, 0)
    high_vol = calculate_position_pct(1000, 0.10, 1.0, 1.0, 0)
    assert high_vol < low_vol


def test_position_size_capped_at_max() -> None:
    size = calculate_position_pct(1000, 0.01, 1.0, 1.0, 0, max_position_pct=0.05)
    assert size <= 0.05


def test_loss_streak_halves_size() -> None:
    normal = calculate_position_pct(1000, 0.02, 1.0, 1.0, 0)
    streak = calculate_position_pct(1000, 0.02, 1.0, 1.0, 2)
    assert streak == normal / 2


def test_risk_off_tightens_exits() -> None:
    trailing, tp = calculate_exit_levels(100.0, 0.05, MarketRegime.RISK_OFF)
    assert trailing == 0.025
    assert tp == 0.05


def test_trending_up_loosens_exits() -> None:
    trailing, tp = calculate_exit_levels(100.0, 0.05, MarketRegime.TRENDING_UP)
    assert trailing > 0.035
    assert tp > 0.08


def test_reduced_risk_state_reduces_size() -> None:
    normal = calculate_position_pct(1000, 0.02, 1.0, 1.0, 0)
    reduced = calculate_position_pct(1000, 0.02, 1.0, 0.5, 0)
    assert reduced == normal * 0.5


def test_missing_atr_uses_max_position_pct_cold_start() -> None:
    # Cold start (ATR unavailable) deploys at the configured max_position_pct,
    # not a hard 1% cap, so capital is used while the price cache warms up.
    assert calculate_position_pct(1000, None, 1.0, 1.0, 0, max_position_pct=0.05) == 0.05
    # Regime/risk multipliers still scale the cold-start size down.
    assert calculate_position_pct(1000, None, 1.0, 0.5, 0, max_position_pct=0.05) == 0.025


def test_zero_equity_returns_zero_edge() -> None:
    assert calculate_position_pct(0.0, 0.02, 1.0, 1.0, 0) == 0.0


def test_position_manager_tightens_trailing_stop_in_profit_steps(tmp_path: Path) -> None:
    settings = Settings(
        position_state_path=str(tmp_path / "positions.json"),
        trailing_stop_pct=0.06,
        take_profit_pct=0.20,
    )
    manager = PositionManager(settings)
    manager.open_position("CAKE", amount_tokens=1.0, entry_price=100.0, entry_value_usdc=100.0)

    assert manager.update_price("CAKE", 108.0) is None
    position = manager.get_position("CAKE")
    assert position is not None
    assert position.current_price == 108.0
    assert round(position.trailing_stop_price, 2) == 103.68

    assert manager.update_price("CAKE", 112.0) is None
    position = manager.get_position("CAKE")
    assert position is not None
    assert position.current_price == 112.0
    assert round(position.trailing_stop_price, 2) == 108.64
