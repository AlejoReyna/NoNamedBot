"""Relative strength scoring against BNB and the tradable universe.

Example:
    result = calculate_relative_strength("CAKE", snapshot)

Interface contract:
    Imports: standard library dataclasses only.
    Exports: RelativeStrengthResult, calculate_relative_strength().
    Does not touch execution, wallets, logs, or live state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelativeStrengthResult:
    """Bounded relative-strength result."""

    symbol: str
    score: float
    vs_bnb: float
    vs_universe_avg: float
    reasons: list[str]


def calculate_relative_strength(
    symbol: str,
    market_snapshot: dict,
    stables: set[str] | None = None,
) -> RelativeStrengthResult:
    """Score a token's 6h performance vs BNB and universe average."""

    stable_symbols = stables or {"USDT", "USDC", "BUSD"}
    normalized = symbol.upper()
    token_data = market_snapshot.get(normalized)
    if not isinstance(token_data, dict):
        return RelativeStrengthResult(normalized, 0.0, 0.0, 0.0, ["missing_symbol"])
    token_change = _optional_float(token_data.get("percent_change_6h"))
    bnb_change = _optional_float(market_snapshot.get("BNB", {}).get("percent_change_6h"))
    universe = _universe_changes(market_snapshot, normalized, stable_symbols)
    if token_change is None or bnb_change is None or not universe:
        return RelativeStrengthResult(normalized, 0.0, 0.0, 0.0, ["insufficient_universe"])

    universe_avg = sum(universe) / len(universe)
    vs_bnb = token_change - bnb_change
    vs_universe = token_change - universe_avg
    raw_score = (0.65 * vs_bnb) + (0.35 * vs_universe)
    score = _clamp(raw_score * 10, -1.0, 1.0)
    reasons: list[str] = []
    reasons.append("outperforming_bnb" if vs_bnb > 0 else "underperforming_bnb")
    reasons.append("outperforming_universe" if vs_universe > 0 else "underperforming_universe")
    return RelativeStrengthResult(normalized, score, vs_bnb, vs_universe, reasons)


def _universe_changes(snapshot: dict, symbol: str, stables: set[str]) -> list[float]:
    excluded = {item.upper() for item in stables} | {"BNB", symbol.upper()}
    values: list[float] = []
    for candidate, data in snapshot.items():
        if str(candidate).upper() in excluded or not isinstance(data, dict):
            continue
        change = _optional_float(data.get("percent_change_6h"))
        if change is not None:
            values.append(change)
    return values


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
