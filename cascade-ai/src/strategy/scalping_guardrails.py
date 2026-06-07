"""Scalping-specific guardrails extending the base Guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.config.settings import Settings
from src.strategy.guardrails import Guardrails, TradeRecord


class ScalpingGuardrails(Guardrails):
    """Guardrails with scalping daily loss cap and consecutive-loss cooldown."""

    def __init__(self, settings: Settings, state_path: str | None = None) -> None:
        self._scalping_consecutive_stops = 0
        self._scalping_cooldown_until: datetime | None = None
        self._scalping_daily_pnl_pct = 0.0
        super().__init__(settings, state_path)

    def check_scalping_daily_loss(self, portfolio_value: float) -> bool:
        """Return True when scalping daily loss cap has been hit."""

        if portfolio_value <= 0:
            return False
        return self._scalping_daily_pnl_pct <= -self.settings.scalping_daily_loss_cap_pct

    def check_consecutive_loss_cooldown(self) -> bool:
        """Return True when consecutive stop-loss cooldown is active."""

        if self._scalping_cooldown_until is None:
            return False
        return self._scalping_cooldown_until > self._now()

    def scalping_entries_allowed(self, portfolio_value: float) -> bool:
        """Return whether scalping mode may open new entries."""

        self._reset_daily_if_needed()
        if self._kill_switch:
            return False
        if self.check_consecutive_loss_cooldown():
            return False
        if self.check_scalping_daily_loss(portfolio_value):
            return False
        if self._paused_until is not None and self._paused_until > self._now():
            return False
        max_trades = self.settings.scalping_max_daily_trades
        return self._daily_trade_count < max_trades

    def record_scalping_trade(
        self,
        record: TradeRecord,
        portfolio_value: float,
        *,
        exit_reason: str | None = None,
    ) -> None:
        """Record a trade and update scalping-specific counters."""

        super().record_trade(record, portfolio_value)
        if record.realized_pnl_usdc != 0 and portfolio_value > 0:
            self._scalping_daily_pnl_pct += record.realized_pnl_usdc / portfolio_value
        if exit_reason == "sl":
            self._scalping_consecutive_stops += 1
            if self._scalping_consecutive_stops >= self.settings.scalping_consecutive_loss_limit:
                hours = self.settings.scalping_consecutive_loss_cooldown_hours
                self._scalping_cooldown_until = self._now() + timedelta(hours=hours)
        elif exit_reason == "tp":
            self._scalping_consecutive_stops = 0

    def _reset_daily_if_needed(self, current_time: datetime | None = None) -> None:
        now = current_time or self._now()
        previous_date = self._daily_date
        super()._reset_daily_if_needed(current_time)
        if now.date() != previous_date:
            self._scalping_daily_pnl_pct = 0.0
            self._scalping_consecutive_stops = 0
            if self._scalping_cooldown_until is not None and self._scalping_cooldown_until <= now:
                self._scalping_cooldown_until = None
