"""Shadow-only Bollinger Band squeeze detector.

Example:
    result = detect_bb_squeeze(closes, atr_values)

Interface contract:
    Imports: standard library dataclasses/statistics only.
    Exports: SqueezeResult, detect_bb_squeeze().
    Does not gate live trades or change position sizes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class SqueezeResult:
    """Bollinger/Keltner squeeze observation."""

    detected: bool
    bb_width: float
    percentile: float
    source: str = "SHADOW"


def detect_bb_squeeze(closes: list[float], atr_values: list[float], period: int = 20) -> SqueezeResult:
    """Detect BB-inside-KC squeeze and rank current BB width."""

    if period <= 1 or len(closes) < period or len(atr_values) < period:
        return SqueezeResult(False, 0.0, 0.0)
    numeric_closes = [float(value) for value in closes]
    numeric_atrs = [float(value) for value in atr_values]
    window = numeric_closes[-period:]
    atr_window = numeric_atrs[-period:]
    mid = sum(window) / period
    std = statistics.pstdev(window)
    atr_avg = sum(atr_window) / period
    bb_upper = mid + (2.0 * std)
    bb_lower = mid - (2.0 * std)
    kc_upper = mid + (1.5 * atr_avg)
    kc_lower = mid - (1.5 * atr_avg)
    width = 0.0 if mid == 0 else (bb_upper - bb_lower) / abs(mid)
    detected = bb_upper < kc_upper and bb_lower > kc_lower
    return SqueezeResult(detected, width, _percentile_width(numeric_closes, period, width))


def _percentile_width(closes: list[float], period: int, current_width: float) -> float:
    widths: list[float] = []
    for index in range(period, len(closes) + 1):
        window = closes[index - period : index]
        mid = sum(window) / period
        if mid == 0:
            widths.append(0.0)
            continue
        widths.append((4.0 * statistics.pstdev(window)) / abs(mid))
    if not widths:
        return 0.0
    lower_or_equal = sum(1 for value in widths if value <= current_width)
    return lower_or_equal / len(widths)
