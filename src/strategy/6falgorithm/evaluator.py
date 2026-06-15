"""Breakout strategy universe evaluator."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from src.config.settings import Settings
from src.execution.twak_interface import TWAKInterface
from src.strategy.candidate_adapter import breakout_decision_to_candidate

_fallback = importlib.import_module("src.strategy.6falgorithm.fallback_scorer")
fallback_scoring_evaluate_universe = _fallback.fallback_scoring_evaluate_universe
from src.strategy.entry_types import EntryCandidate
from src.strategy.guardrails import RiskDecision
from src.strategy.regime_detector import RegimeResult

_breakout_module = importlib.import_module("src.strategy.6falgorithm.breakout_engine")
BreakoutEngine = _breakout_module.BreakoutEngine

LOGGER = logging.getLogger(__name__)


def evaluate_universe_breakout(
    snapshot: dict[str, dict[str, Any]],
    portfolio_value: float,
    regime_result: RegimeResult,
    risk_decision: RiskDecision,
    *,
    settings: Settings,
    twak_interface: TWAKInterface | None = None,
    exclude_symbols: set[str] | None = None,
    use_breakout_engine: bool = True,
) -> EntryCandidate | None:
    """Evaluate the universe using the 6-factor BreakoutEngine or legacy fallback."""

    if not use_breakout_engine:
        return fallback_scoring_evaluate_universe(
            snapshot,
            portfolio_value,
            regime_result,
            risk_decision,
            settings=settings,
            twak_interface=twak_interface,
            exclude_symbols=exclude_symbols,
        )

    engine = BreakoutEngine(settings, twak_interface)
    filtered_snapshot = {
        symbol: data
        for symbol, data in snapshot.items()
        if symbol.upper() not in {item.upper() for item in (exclude_symbols or set())}
    }

    decisions = engine.evaluate_all(filtered_snapshot, portfolio_value)
    passers = [decision for decision in decisions if decision.should_enter]
    if not passers:
        return None

    selected = passers[0]
    return breakout_decision_to_candidate(
        selected,
        snapshot,
        portfolio_value,
        settings,
        risk_decision,
    )
