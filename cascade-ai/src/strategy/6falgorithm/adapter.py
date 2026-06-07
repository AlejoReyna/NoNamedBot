"""Conversion helpers between breakout decisions and entry candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import importlib

from src.config.settings import Settings
from src.strategy.entry_types import EntryCandidate

_breakout = importlib.import_module("src.strategy.6falgorithm.breakout_engine")
BreakoutDecision = _breakout.BreakoutDecision
from src.strategy.guardrails import RiskDecision


def maybe_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def decimal_div(numerator: float, denominator: float) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(str(numerator)) / Decimal(str(denominator))


def first_market_number(data: dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
    for key in keys:
        number = maybe_number(data.get(key))
        if number is not None:
            return number
    return default


def breakout_decision_to_candidate(
    decision: BreakoutDecision,
    market_snapshot: dict[str, dict[str, Any]],
    portfolio_value: float,
    settings: Settings,
    risk_decision: RiskDecision,
) -> EntryCandidate | None:
    """Convert a BreakoutDecision into an EntryCandidate when signal is present."""

    if not decision.should_enter or decision.symbol is None:
        return None
    symbol = decision.symbol.upper()
    token_data = market_snapshot.get(symbol, {})
    price = maybe_number(token_data.get("price"))
    if price is None or price <= 0:
        return None
    position_size = decision.position_size_usdc
    if position_size <= 0:
        position_size = portfolio_value * settings.max_position_pct * risk_decision.position_multiplier
    slippage_normal = decision.estimated_slippage_pct
    slippage_small = maybe_number(token_data.get("estimated_slippage_small_pct"))
    if slippage_small is None and slippage_normal is not None:
        slippage_small = max(0.0, slippage_normal * 0.5)
    return EntryCandidate(
        symbol=symbol,
        price=price,
        position_size_usdc=position_size,
        expected_amount_out=decimal_div(position_size, price),
        slippage_small=slippage_small,
        slippage_normal=slippage_normal,
        reason=decision.reason,
        factor_scores=dict(decision.factor_scores),
        true_factor_count=decision.true_factor_count,
        source="breakout_engine",
        strategy_mode="breakout",
    )


def coerce_entry_candidate(
    candidate: Any,
    portfolio_value: float,
    settings: Settings,
    risk_decision: RiskDecision,
) -> EntryCandidate | None:
    if candidate is None:
        return None
    if isinstance(candidate, EntryCandidate):
        return candidate
    symbol = getattr(candidate, "symbol", None)
    price = maybe_number(getattr(candidate, "price", None))
    if symbol is None or price is None or price <= 0:
        return None
    position_size = maybe_number(getattr(candidate, "position_size_usdc", None))
    if position_size is None:
        position_size = portfolio_value * settings.max_position_pct * risk_decision.position_multiplier
    expected = getattr(candidate, "expected_amount_out", None)
    expected_amount_out = Decimal(str(expected)) if expected is not None else decimal_div(position_size, price)
    return EntryCandidate(
        symbol=str(symbol).upper(),
        price=price,
        position_size_usdc=position_size,
        expected_amount_out=expected_amount_out,
        slippage_small=maybe_number(getattr(candidate, "slippage_small", None)),
        slippage_normal=maybe_number(getattr(candidate, "slippage_normal", None)),
        reason=str(getattr(candidate, "reason", "scoring candidate")),
        factor_scores=dict(getattr(candidate, "factor_scores", {}) or {}),
        true_factor_count=int(getattr(candidate, "true_factor_count", 0) or 0),
        source=str(getattr(candidate, "source", "scoring")),
        entry_score=maybe_number(getattr(candidate, "entry_score", None)),
        strategy_mode=str(getattr(candidate, "strategy_mode", "breakout")),
    )
