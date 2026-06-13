"""Scalping position manager with fixed TP/SL, time stops, and symbol cooldown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.settings import Settings
from src.strategy.position_manager import Position, PositionManager
from src.strategy.volatility import PriceCache


@dataclass
class ScalpingExitSignal:
    """Pending exit triggered by scalping rules."""

    symbol: str
    reason: str
    current_price: float


class ScalpingPositionManager(PositionManager):
    """Fixed TP/SL scalping exits without trailing stops."""

    def __init__(self, settings: Settings, state_path: str | None = None) -> None:
        super().__init__(settings, state_path)
        self._symbol_cooldowns: dict[str, datetime] = {}
        self._pending_exits: list[ScalpingExitSignal] = []

    def open_position(
        self,
        symbol: str,
        amount_tokens: float,
        entry_price: float,
        entry_value_usdc: float,
    ) -> Position:
        normalized = symbol.upper()
        if self.is_symbol_on_cooldown(normalized):
            raise ValueError(f"{normalized} is on post-close cooldown")
        now = datetime.now(timezone.utc)
        position = Position(
            symbol=normalized,
            amount_tokens=amount_tokens,
            entry_price=entry_price,
            entry_value_usdc=entry_value_usdc,
            highest_price=entry_price,
            trailing_stop_price=entry_price * (1 - self.settings.scalping_stop_loss_pct),
            take_profit_price=entry_price * (1 + self.settings.scalping_take_profit_pct),
            opened_at=now,
            current_price=entry_price,
            current_price_at=now,
        )
        self._positions[normalized] = position
        self.persist_positions()
        return position

    def close_position(self, symbol: str) -> Position | None:
        position = super().close_position(symbol)
        if position is not None:
            cooldown = timedelta(minutes=self.settings.scalping_symbol_cooldown_minutes)
            self._symbol_cooldowns[position.symbol] = datetime.now(timezone.utc) + cooldown
        return position

    def is_symbol_on_cooldown(self, symbol: str) -> bool:
        normalized = symbol.upper()
        until = self._symbol_cooldowns.get(normalized)
        if until is None:
            return False
        if until <= datetime.now(timezone.utc):
            self._symbol_cooldowns.pop(normalized, None)
            return False
        return True

    def check_exits(
        self,
        market_snapshot: dict[str, dict[str, Any]],
        price_cache: PriceCache | None = None,
    ) -> None:
        del price_cache
        self._pending_exits.clear()
        now = datetime.now(timezone.utc)
        should_persist = False
        for position in list(self.list_open_positions()):
            token_data = market_snapshot.get(position.symbol, {})
            current_price = self._snapshot_price(token_data, position.entry_price)
            position.current_price = current_price
            position.current_price_at = now
            should_persist = True
            exit_reason = self._evaluate_exit(position, current_price, now)
            if exit_reason is not None:
                self._pending_exits.append(
                    ScalpingExitSignal(
                        symbol=position.symbol,
                        reason=exit_reason,
                        current_price=current_price,
                    )
                )
        if should_persist:
            self.persist_positions()

    def pop_pending_exit(self) -> ScalpingExitSignal | None:
        if not self._pending_exits:
            return None
        return self._pending_exits.pop(0)

    def hold_time_seconds(self, symbol: str) -> int | None:
        position = self.get_position(symbol)
        if position is None:
            return None
        delta = datetime.now(timezone.utc) - position.opened_at
        return int(delta.total_seconds())

    def _evaluate_exit(self, position: Position, current_price: float, now: datetime) -> str | None:
        entry = position.entry_price
        if entry <= 0:
            return None
        pnl_pct = (current_price - entry) / entry
        hold_minutes = (now - position.opened_at).total_seconds() / 60.0

        if pnl_pct >= self.settings.scalping_take_profit_pct:
            return "tp"
        if pnl_pct <= -self.settings.scalping_stop_loss_pct:
            return "sl"
        if hold_minutes >= self.settings.scalping_max_hold_minutes:
            return "max_hold"
        if hold_minutes >= self.settings.scalping_time_stop_minutes:
            if -0.005 <= pnl_pct <= 0.005:
                return "time_stop"
        return None

    @staticmethod
    def _snapshot_price(token_data: dict[str, Any], fallback: float) -> float:
        try:
            price = float(token_data.get("price", fallback))
            return price if price > 0 else fallback
        except (TypeError, ValueError):
            return fallback
