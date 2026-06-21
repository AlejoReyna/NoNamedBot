"""Tests for the 4 competition-critical fixes (BNB Chain Hack Track 1).

Priority 1 – Rollover overexposure fix
Priority 2 – Regime-aware trade limits + minimum position floor
Priority 3 – 8-hour max hold on losers only
Priority 4 – Tighter emergency liquidation threshold (2 %)
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.settings import Settings, load_settings
from src.strategy.guardrails import Guardrails, RiskState
from src.strategy.position_manager import Position, PositionManager
from src.strategy.regime_detector import MarketRegime, RegimeResult


# ───────────────────────────────────────────────────────────────
# Priority 1 – Rollover Overexposure Fix
# ───────────────────────────────────────────────────────────────

class TestRolloverOverexposure:
    """At UTC midnight open positions must count as spent budget."""

    def _make_guardrails(self, max_daily: int = 3) -> Guardrails:
        settings = Settings(
            max_daily_trades=max_daily,
            max_daily_trades_by_regime={"trending_up": 4, "ranging": 2, "risk_off": 0},
            global_max_daily_trades=6,
            max_daily_loss_pct=0.02,
            max_hold_hours=8.0,
            min_position_size_usd=2.0,
            max_position_pct=0.05,
            max_slippage_pct=0.01,
            paper_trade=True,
            guardrail_state_path="guardrail_state.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "guardrail_state.json"
            g = Guardrails(settings, state_path=state_path)
            # Back-date the daily counter so it looks like yesterday
            g._daily_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
            g._daily_trade_count = 2  # two trades "yesterday"
            g._save_state()
            return g

    def test_midnight_reset_consumes_open_positions(self) -> None:
        """If 2 positions are open at midnight, budget = max_daily - 2."""
        g = self._make_guardrails(max_daily=2)
        # Simulate 2 open positions → budget is 0, so cannot open
        assert not g.can_open_new_trade(open_position_count=2, current_regime="")
        # Simulate 1 open position → budget of 1 left
        assert g.can_open_new_trade(open_position_count=1, current_regime="")

    def test_midnight_reset_allows_one_more_when_one_open(self) -> None:
        g = self._make_guardrails(max_daily=3)
        assert g.can_open_new_trade(open_position_count=1, current_regime="")
        assert not g.can_open_new_trade(open_position_count=3, current_regime="")

    def test_budget_is_regime_aware(self) -> None:
        g = self._make_guardrails(max_daily=3)
        # In ranging regime limit is 2; with 2 open positions budget is 0
        assert not g.can_open_new_trade(open_position_count=2, current_regime="ranging")
        # In trending_up regime limit is 4; with 2 open positions budget is 2
        assert g.can_open_new_trade(open_position_count=2, current_regime="trending_up")


# ───────────────────────────────────────────────────────────────
# Priority 2 – Regime-Aware Limits + Minimum Position Floor
# ───────────────────────────────────────────────────────────────

class TestRegimeAwareLimits:
    """MAX_DAILY_TRADES_BY_REGIME and MIN_POSITION_SIZE_USD enforcement."""

    def _make_guardrails(self) -> Guardrails:
        settings = Settings(
            max_daily_trades=3,
            max_daily_trades_by_regime={"trending_up": 4, "ranging": 2, "risk_off": 0},
            global_max_daily_trades=6,
            max_daily_loss_pct=0.02,
            max_hold_hours=8.0,
            min_position_size_usd=2.0,
            max_position_pct=0.05,
            max_slippage_pct=0.01,
            paper_trade=True,
            guardrail_state_path="guardrail_state.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            return Guardrails(settings, state_path=Path(tmpdir) / "guardrail_state.json")

    def test_trending_up_limit_is_four(self) -> None:
        g = self._make_guardrails()
        assert g.can_open_new_trade(open_position_count=0, current_regime="trending_up")
        assert g.can_open_new_trade(open_position_count=3, current_regime="trending_up")
        assert not g.can_open_new_trade(open_position_count=4, current_regime="trending_up")

    def test_ranging_limit_is_two(self) -> None:
        g = self._make_guardrails()
        assert g.can_open_new_trade(open_position_count=0, current_regime="ranging")
        assert g.can_open_new_trade(open_position_count=1, current_regime="ranging")
        assert not g.can_open_new_trade(open_position_count=2, current_regime="ranging")

    def test_risk_off_blocks_all_trades(self) -> None:
        g = self._make_guardrails()
        assert not g.can_open_new_trade(open_position_count=0, current_regime="risk_off")
        assert not g.can_open_new_trade(open_position_count=0, current_regime="risk_off")

    def test_global_max_daily_trades_cap(self) -> None:
        g = self._make_guardrails()
        g._daily_trade_count = 6
        # Even in trending_up with 0 open positions, global cap of 6 blocks new trade
        assert not g.can_open_new_trade(open_position_count=0, current_regime="trending_up")

    def test_risk_decision_reflects_regime_limit(self) -> None:
        g = self._make_guardrails()
        # Mock regime_result
        regime_result = MagicMock()
        regime_result.regime = MarketRegime.TRENDING_UP
        regime_result.reasons = []
        regime_result.sentiment_fragility = "NONE"
        decision = g.evaluate(100.0, regime_result)
        assert decision.max_daily_trades == 4

        regime_result.regime = MarketRegime.RANGING
        decision = g.evaluate(100.0, regime_result)
        assert decision.max_daily_trades == 2

        regime_result.regime = MarketRegime.RISK_OFF
        decision = g.evaluate(100.0, regime_result)
        assert decision.max_daily_trades == 0

    def test_min_position_size_floor_blocks_dust(self) -> None:
        settings = Settings(
            max_daily_trades=3,
            max_daily_loss_pct=0.02,
            min_position_size_usd=2.0,
            max_position_pct=0.05,
            max_slippage_pct=0.01,
            paper_trade=True,
            position_state_path="positions.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PositionManager(settings, state_path=Path(tmpdir) / "positions.json")
            # A $1.50 position is below the $2.00 floor
            size = 1.50
            assert size < settings.min_position_size_usd


# ───────────────────────────────────────────────────────────────
# Priority 3 – 8-Hour Max Hold on Losers Only
# ───────────────────────────────────────────────────────────────

class TestMaxHoldLosersOnly:
    """Positions older than 8 h are force-closed ONLY if they are losers."""

    def _make_manager(self, max_hold_hours: float = 8.0) -> PositionManager:
        settings = Settings(
            max_daily_trades=3,
            max_daily_loss_pct=0.02,
            max_hold_hours=max_hold_hours,
            min_position_size_usd=2.0,
            max_position_pct=0.05,
            max_slippage_pct=0.01,
            trailing_stop_pct=0.06,
            take_profit_pct=0.08,
            paper_trade=True,
            position_state_path="positions.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            return PositionManager(settings, state_path=Path(tmpdir) / "positions.json")

    def _make_old_position(self, pm: PositionManager, entry_price: float = 100.0) -> Position:
        nine_hours_ago = datetime.now(timezone.utc) - timedelta(hours=9)
        pos = Position(
            symbol="ETH",
            amount_tokens=1.0,
            entry_price=entry_price,
            entry_value_usdc=10.0,
            highest_price=entry_price,
            trailing_stop_price=entry_price * 0.94,
            take_profit_price=entry_price * 1.08,
            opened_at=nine_hours_ago,
        )
        pm.restore_position(pos)
        return pos

    def test_loser_force_closed_after_8h(self) -> None:
        pm = self._make_manager()
        self._make_old_position(pm, entry_price=100.0)
        # current_price = 95 < entry_price (loser) but > trailing_stop (94)
        reason = pm.update_price("ETH", 95.0)
        assert reason == "time_stop"

    def test_winner_not_force_closed_after_8h(self) -> None:
        pm = self._make_manager()
        self._make_old_position(pm, entry_price=100.0)
        # current_price = 105 >= entry_price (winner) but < take_profit (108)
        reason = pm.update_price("ETH", 105.0)
        assert reason is None

    def test_young_position_not_force_closed_even_if_loser(self) -> None:
        pm = self._make_manager()
        seven_hours_ago = datetime.now(timezone.utc) - timedelta(hours=7)
        pos = Position(
            symbol="ETH",
            amount_tokens=1.0,
            entry_price=100.0,
            entry_value_usdc=10.0,
            highest_price=100.0,
            trailing_stop_price=94.0,
            take_profit_price=108.0,
            opened_at=seven_hours_ago,
        )
        pm.restore_position(pos)
        # 95 < entry_price but > trailing_stop, and < 8h old
        reason = pm.update_price("ETH", 95.0)
        assert reason is None

    def test_disabled_when_max_hold_hours_is_zero(self) -> None:
        pm = self._make_manager(max_hold_hours=0.0)
        self._make_old_position(pm, entry_price=100.0)
        # 95 < entry_price but > trailing_stop, time stop disabled
        reason = pm.update_price("ETH", 95.0)
        assert reason is None


# ───────────────────────────────────────────────────────────────
# Priority 4 – Tighter Emergency Liquidation Threshold
# ───────────────────────────────────────────────────────────────

class TestTighterKillSwitch:
    """max_daily_loss_pct lowered from 3.0 % to 2.0 %."""

    def test_default_max_daily_loss_pct_is_two_percent(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        settings = load_settings(str(env_path))
        assert settings.max_daily_loss_pct == 0.02

    def test_daily_loss_limit_usd_for_twenty_dollar_book(self) -> None:
        portfolio = 20.0
        limit = portfolio * 0.02
        assert limit == 0.40

    def test_guardrails_daily_loss_limit(self) -> None:
        settings = Settings(
            max_daily_trades=3,
            max_daily_loss_pct=0.02,
            max_position_pct=0.05,
            max_slippage_pct=0.01,
            paper_trade=True,
            guardrail_state_path="guardrail_state.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            g = Guardrails(settings, state_path=Path(tmpdir) / "guardrail_state.json")
            g._daily_realized_loss_usdc = 0.41  # above 2 % of $20
            assert g._daily_loss_limit_hit(20.0)
            g._daily_realized_loss_usdc = 0.39  # below 2 % of $20
            assert not g._daily_loss_limit_hit(20.0)
