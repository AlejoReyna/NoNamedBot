"""Compatibility shim — implementation lives in src.strategy.6falgorithm."""

from __future__ import annotations

import importlib

_module = importlib.import_module("src.strategy.6falgorithm.breakout_engine")

BreakoutDecision = _module.BreakoutDecision
BreakoutEngine = _module.BreakoutEngine
LocalCache = _module.LocalCache
CORE_FACTOR_COUNT = _module.CORE_FACTOR_COUNT
TOTAL_FACTOR_COUNT = _module.TOTAL_FACTOR_COUNT

__all__ = [
    "BreakoutDecision",
    "BreakoutEngine",
    "LocalCache",
    "CORE_FACTOR_COUNT",
    "TOTAL_FACTOR_COUNT",
]
