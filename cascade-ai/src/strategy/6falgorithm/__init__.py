"""Six-factor breakout algorithm package."""

from __future__ import annotations

import importlib

_breakout = importlib.import_module("src.strategy.6falgorithm.breakout_engine")

BreakoutDecision = _breakout.BreakoutDecision
BreakoutEngine = _breakout.BreakoutEngine
LocalCache = _breakout.LocalCache
CORE_FACTOR_COUNT = _breakout.CORE_FACTOR_COUNT
TOTAL_FACTOR_COUNT = _breakout.TOTAL_FACTOR_COUNT

__all__ = [
    "BreakoutDecision",
    "BreakoutEngine",
    "LocalCache",
    "CORE_FACTOR_COUNT",
    "TOTAL_FACTOR_COUNT",
]
