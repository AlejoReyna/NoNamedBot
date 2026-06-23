"""Tests for risk guardrails."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.strategy.guardrails import Guardrails, TradeRecord


def _settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(guardrail_state_path=str(tmp_path / "guardrail_state.json"), **kwargs)


def test_rejects_symbol_not_in_target_allowlist(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    with pytest.raises(ValueError):
        guardrails.validate_new_trade("BNB", 100.0, 10000.0, 0.001)


def test_rejects_stablecoin_as_directional_trade(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    with pytest.raises(ValueError):
        guardrails.validate_new_trade("USDC", 100.0, 10000.0, 0.001)


def test_rejects_position_over_five_percent(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    with pytest.raises(ValueError):
        guardrails.validate_new_trade("CAKE", 501.0, 10000.0, 0.001)


def test_rejects_slippage_over_one_percent(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    with pytest.raises(ValueError):
        guardrails.validate_new_trade("CAKE", 100.0, 10000.0, 0.011)


def test_rejects_negative_slippage(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    with pytest.raises(ValueError, match="slippage"):
        guardrails.validate_new_trade("CAKE", 100.0, 10000.0, -0.001)


def test_accepts_zero_slippage_from_dex_price_impact(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    guardrails.validate_new_trade("CAKE", 100.0, 10000.0, 0.0)


def test_max_daily_trades_blocks_fourth_trade(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path, max_daily_trades=3))
    now = datetime.now(timezone.utc)
    for _ in range(3):
        guardrails.record_trade(
            TradeRecord("CAKE", "buy", 100.0, 0.0, now),
            portfolio_value_usdc=10000.0,
        )
    assert guardrails.can_open_new_trade() is False
    with pytest.raises(RuntimeError):
        guardrails.validate_new_trade("CAKE", 100.0, 10000.0, 0.001)


def test_drawdown_kill_switch_triggers_at_eighteen_percent(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings(tmp_path))
    assert guardrails.update_portfolio_value(10000.0) is False
    assert guardrails.update_portfolio_value(8200.0) is True
    assert guardrails.should_kill_switch() is True


def test_guardrail_state_initializes_and_persists(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state_path = Path(settings.guardrail_state_path)

    guardrails = Guardrails(settings)
    assert state_path.exists()

    guardrails.record_trade(
        TradeRecord("CAKE", "buy", 100.0, 0.0, datetime.now(timezone.utc)),
        portfolio_value_usdc=10000.0,
    )
    guardrails.record_trade(
        TradeRecord("CAKE", "sell", 90.0, -10.0, datetime.now(timezone.utc)),
        portfolio_value_usdc=10000.0,
    )
    guardrails.update_portfolio_value(12345.0)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["daily_trade_count"] == 1
    assert payload["daily_realized_loss"] == 10.0
    assert payload["portfolio_ath"] == 12345.0
    assert payload["last_reset_date"] == datetime.now(timezone.utc).date().isoformat()

    reloaded = Guardrails(settings)
    with pytest.raises(RuntimeError, match="daily realized loss"):
        reloaded.validate_new_trade("CAKE", 1.0, 100.0, 0.001)


def test_guardrail_state_resets_daily_counts_after_date_change(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state_path = Path(settings.guardrail_state_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    state_path.write_text(
        json.dumps(
            {
                "daily_trade_count": 3,
                "daily_realized_loss": 50.0,
                "portfolio_ath": 10000.0,
                "last_reset_date": yesterday,
            }
        ),
        encoding="utf-8",
    )

    guardrails = Guardrails(settings)

    assert guardrails.can_open_new_trade() is True
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["daily_trade_count"] == 0
    assert payload["daily_realized_loss"] == 0.0
    assert payload["portfolio_ath"] == 10000.0


class _StubRegime:
    """Minimal regime_result stand-in for Guardrails.evaluate()."""

    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.sentiment_fragility = "NONE"


def _seed_state(tmp_path: Path, **fields: object) -> Path:
    state_path = tmp_path / "guardrail_state.json"
    payload = {
        "daily_trade_count": 0,
        "daily_realized_loss": 0.0,
        "portfolio_ath": 0.0,
        "kill_switch": False,
        "last_reset_date": datetime.now(timezone.utc).date().isoformat(),
    }
    payload.update(fields)
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return state_path


def test_stale_ath_latches_kill_switch_documented(tmp_path: Path) -> None:
    """Documents the bug: a stale ATH of 10000 vs an ~8k paper balance is a 20%
    drawdown, which exceeds the 18% kill switch and latches it."""

    state_path = _seed_state(tmp_path, portfolio_ath=10000.0)
    settings = Settings(guardrail_state_path=str(state_path), paper_trade=True)
    guardrails = Guardrails(settings)

    decision = guardrails.evaluate(8000.0, _StubRegime())

    assert decision.state.value == "kill_switch"
    assert "drawdown_kill_switch" in decision.reasons
    assert guardrails.should_kill_switch() is True
    # And it stays latched on the next cycle even at the same value.
    assert guardrails.evaluate(8000.0, _StubRegime()).state.value == "kill_switch"


def test_recalibrate_paper_state_clears_stale_kill_switch(tmp_path: Path) -> None:
    state_path = _seed_state(tmp_path, portfolio_ath=10000.0, kill_switch=True)
    settings = Settings(guardrail_state_path=str(state_path), paper_trade=True)
    guardrails = Guardrails(settings)
    assert guardrails.should_kill_switch() is True

    result = guardrails.recalibrate_paper_state(8000.0)

    assert result["before"]["kill_switch"] is True
    assert guardrails.should_kill_switch() is False
    assert guardrails.all_time_high_usdc == 8000.0
    # Persisted, and a fresh cycle at the re-anchored value is NORMAL again.
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["portfolio_ath"] == 8000.0
    assert persisted["kill_switch"] is False
    assert guardrails.evaluate(8000.0, _StubRegime()).state.value == "normal"


def test_recalibrate_refused_in_live_mode(tmp_path: Path) -> None:
    state_path = _seed_state(tmp_path, portfolio_ath=10000.0, kill_switch=True)
    settings = Settings(guardrail_state_path=str(state_path), paper_trade=False)
    guardrails = Guardrails(settings)

    with pytest.raises(RuntimeError, match="paper_trade is False"):
        guardrails.recalibrate_paper_state(8000.0)

    # Live drawdown state must be left untouched.
    assert guardrails.should_kill_switch() is True
    assert guardrails.all_time_high_usdc == 10000.0
