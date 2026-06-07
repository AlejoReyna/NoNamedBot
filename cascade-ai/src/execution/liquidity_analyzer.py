"""Quote-derived liquidity analysis for deterministic live gates.

Example:
    result = analyze_liquidity("CAKE", 100.0, 0.001, 0.002, 0.01)

Interface contract:
    Imports: standard library dataclasses only.
    Exports: LiquidityResult, analyze_liquidity().
    Does not call APIs, execute swaps, or read wallet state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiquidityResult:
    """Liquidity recommendation derived from TWAK quote slippage."""

    symbol: str
    liquidity_score: float
    slippage_small: float | None
    slippage_normal: float | None
    slippage_curve_convex: bool
    recommendation: str


def analyze_liquidity(
    symbol: str,
    position_usd: float,
    twak_quote_small: float | None,
    twak_quote_normal: float | None,
    max_slippage_pct: float,
) -> LiquidityResult:
    """Return PROCEED, REDUCE_SIZE, or REJECT from quote slippage."""

    normalized = symbol.upper()
    small = _valid_slippage(twak_quote_small)
    normal = _valid_slippage(twak_quote_normal)
    if position_usd <= 0 or max_slippage_pct <= 0 or normal is None:
        return LiquidityResult(normalized, 0.0, small, normal, False, "REJECT")

    convex = small is not None and small * 5 < normal
    score = max(0.0, min(1.0, 1 - (normal / max_slippage_pct)))
    if normal > max_slippage_pct:
        return LiquidityResult(normalized, score, small, normal, convex, "REJECT")
    if convex:
        return LiquidityResult(normalized, max(0.0, score * 0.75), small, normal, convex, "REDUCE_SIZE")
    return LiquidityResult(normalized, score, small, normal, convex, "PROCEED")


def _valid_slippage(value: float | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed < 0:
        return None
    return parsed
