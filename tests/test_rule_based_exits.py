"""Tests for rule-based sizing, volatility-aware targets, and realization rules.

Covers the three coupled changes:
  1. open_position derives exit levels from ATR/regime when context is supplied
     (no more flat +15% target on low-vol large caps), and keeps flat
     settings-based levels for legacy callers.
  2. calculate_position_pct stays risk-based on cold ATR (assumed stop distance)
     instead of deploying a flat max position.
  3. A max-hold time-stop forces turnover; the competition-window flatten
     liquidates the book before the deadline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.strategy.position_manager import (
    PositionManager,
    calculate_exit_levels,
    calculate_position_pct,
)
from src.strategy.regime_detector import MarketRegime


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(position_state_path=str(tmp_path / "positions.json"))
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- 1. Volatility-aware targets ------------------------------------------


def test_open_position_with_regime_uses_volatility_aware_levels(tmp_path: Path) -> None:
    # ETH-style: ATR cold, but regime context supplied -> target must NOT be
    # the flat +15%. calculate_exit_levels falls back to 8% TP, not 15%.
    settings = _settings(tmp_path, take_profit_pct=0.15, trailing_stop_pct=0.06)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "ETH",
        amount_tokens=1.0,
        entry_price=1683.08,
        position_usd=100.0,
        atr_pct=None,
        regime=MarketRegime.TRENDING_UP,
    )
    target_pct = pos.take_profit_price / pos.entry_price - 1.0
    assert target_pct < 0.15  # the miracle +15% is gone
    assert round(target_pct, 4) == 0.08


def test_open_position_target_scales_with_atr(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = PositionManager(settings)
    low_vol = manager.open_position(
        "ETH", amount_tokens=1.0, entry_price=1000.0, position_usd=100.0,
        atr_pct=0.02, regime=MarketRegime.TRENDING_UP,
    )
    manager.close_position("ETH")
    high_vol = manager.open_position(
        "DOGE", amount_tokens=1.0, entry_price=1.0, position_usd=100.0,
        atr_pct=0.06, regime=MarketRegime.TRENDING_UP,
    )
    low_target = low_vol.take_profit_price / low_vol.entry_price - 1.0
    high_target = high_vol.take_profit_price / high_vol.entry_price - 1.0
    # A volatile microcap earns a wider target than a calm large cap.
    assert high_target > low_target


def test_open_position_legacy_callers_keep_flat_levels(tmp_path: Path) -> None:
    settings = _settings(tmp_path, take_profit_pct=0.20, trailing_stop_pct=0.06)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "CAKE", amount_tokens=1.0, entry_price=100.0, entry_value_usdc=100.0,
    )
    assert round(pos.take_profit_price, 2) == 120.0
    assert round(pos.trailing_stop_price, 2) == 94.0


# --- 2. Risk-based sizing on cold ATR -------------------------------------


def test_cold_atr_sizing_is_risk_based_not_flat() -> None:
    # With a risk budget whose risk-based size sits below the cap, the
    # cold-start size must equal that risk-based value (budget / assumed stop)
    # -- not the flat max position.
    max_pct = 0.20
    size = calculate_position_pct(
        1000, None, 1.0, 1.0, 0,
        max_position_pct=max_pct, base_risk_per_trade_pct=0.008, fallback_stop_pct=0.06,
    )
    assert round(size, 4) == round(0.008 / 0.06, 4)
    assert size < max_pct  # proves it is risk-based, not pinned to the cap


def test_cold_atr_wider_assumed_stop_means_smaller_size() -> None:
    tight = calculate_position_pct(
        1000, None, 1.0, 1.0, 0, max_position_pct=0.50,
        base_risk_per_trade_pct=0.02, fallback_stop_pct=0.04,
    )
    wide = calculate_position_pct(
        1000, None, 1.0, 1.0, 0, max_position_pct=0.50,
        base_risk_per_trade_pct=0.02, fallback_stop_pct=0.10,
    )
    assert wide < tight


# --- 3a. Time-stop ---------------------------------------------------------


def test_time_stop_fires_after_max_hold(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_hold_hours=12.0, take_profit_pct=0.20)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    # Backdate the open so the position is stale.
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=13)
    # Price sits between stop and target -> only the time-stop can fire.
    reason = manager.update_price("DOT", 1.0)
    assert reason == "time_stop"


def test_time_stop_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)  # max_hold_hours defaults to 0
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=100)
    assert manager.update_price("DOT", 1.0) is None


def test_target_still_wins_over_time_stop(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_hold_hours=1.0)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=99)
    # Price above target -> take_profit takes precedence over time_stop.
    assert manager.update_price("DOT", 5.0) == "take_profit"


# --- 3b. Competition-window flatten ---------------------------------------


def test_window_flatten_helper(tmp_path: Path) -> None:
    from src import main as main_mod

    sells: list[str] = []

    class _Router:
        pass

    class _FakeManager:
        def __init__(self) -> None:
            self._open = ["ETH", "DOGE"]

        def list_open_positions(self):
            return list(self._open)

    class _Guardrails:
        pass

    captured = {}

    def _fake_liquidate(pm, router, guardrails, toolkit=None):  # noqa: ANN001
        del router, guardrails, toolkit
        captured["called"] = True
        pm._open.clear()

    orig = main_mod.emergency_liquidate
    main_mod.emergency_liquidate = _fake_liquidate
    try:
        end = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
        settings = _settings(
            tmp_path,
            competition_end_utc=end.isoformat(),
            flatten_before_end_minutes=30,
        )
        pm = _FakeManager()
        # Well before the window -> no flatten.
        assert main_mod._maybe_flatten_for_window(
            settings, pm, _Router(), _Guardrails(), end - timedelta(hours=2)
        ) is False
        assert "called" not in captured
        # Inside the flatten window -> flatten fires and entries are blocked.
        assert main_mod._maybe_flatten_for_window(
            settings, pm, _Router(), _Guardrails(), end - timedelta(minutes=10)
        ) is True
        assert captured.get("called") is True
    finally:
        main_mod.emergency_liquidate = orig


def test_window_flatten_disabled_when_unset(tmp_path: Path) -> None:
    from src import main as main_mod

    settings = _settings(tmp_path, competition_end_utc="")

    class _PM:
        def list_open_positions(self):
            return []

    assert main_mod._maybe_flatten_for_window(
        settings, _PM(), object(), object(), datetime.now(timezone.utc)
    ) is False


# --- 3c. Exit resilience: a reverting swap must not crash the agent ---------


def test_failed_exit_swap_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    """A reverting/failed exit swap must be caught: the position stays open and
    the agent keeps running, instead of an uncaught RuntimeError crash-looping
    the process (the dust-ATOM time-stop bug found in production)."""
    from src import main as main_mod

    settings = _settings(tmp_path)
    manager = PositionManager(settings)
    manager.open_position(
        "ATOM", amount_tokens=0.0528, entry_price=1.985, position_usd=0.10,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("twak swap failed with exit code 1: execution reverted")

    monkeypatch.setattr(main_mod, "_execute_logged_swap", _boom)

    class _Guardrails:
        pass

    guardrails = _Guardrails()
    guardrails.settings = settings

    # Must NOT raise, and the position must remain open for a later retry.
    main_mod._execute_position_exit(
        manager, object(), guardrails, "ATOM", 1.95, 100.0, exit_reason="time_stop"
    )
    assert manager.get_position("ATOM") is not None


def test_exit_swap_caps_to_live_wallet_balance(tmp_path: Path, monkeypatch) -> None:
    from src import main as main_mod

    settings = _settings(tmp_path)
    manager = PositionManager(settings)
    manager.open_position(
        "DOGE",
        amount_tokens=5.59594069,
        entry_price=0.089,
        position_usd=0.498,
        atr_pct=0.03,
        regime=MarketRegime.TRENDING_UP,
    )
    calls: list[dict[str, object]] = []

    class _Guardrails:
        def __init__(self) -> None:
            self.settings = settings

        def record_trade(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            del args, kwargs

    class _Toolkit:
        def get_balance(self, symbol: str) -> dict[str, object]:
            assert symbol == "DOGE"
            return {"symbol": "DOGE", "balance": 5.59530611}

    def _swap(
        settings_arg,
        router,
        action,
        from_symbol,
        to_symbol,
        amount_in,
        max_slippage_pct,
        expected_amount_out=None,
        **kwargs,
    ):
        del settings_arg, router, action, to_symbol, max_slippage_pct, kwargs
        calls.append(
            {
                "from_symbol": from_symbol,
                "amount_in": amount_in,
                "expected_amount_out": expected_amount_out,
            }
        )
        return {"tx_hash": "0x" + "1" * 64}

    monkeypatch.setattr(main_mod, "_execute_logged_swap", _swap)

    main_mod._execute_position_exit(
        manager,
        object(),
        _Guardrails(),
        "DOGE",
        0.086,
        100.0,
        exit_reason="time_stop",
        toolkit=_Toolkit(),
    )

    assert calls == [
        {
            "from_symbol": "DOGE",
            "amount_in": pytest.approx(5.59530611),
            "expected_amount_out": pytest.approx(5.59530611 * 0.086),
        }
    ]
    assert manager.get_position("DOGE") is None
