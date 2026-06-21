"""Event-driven planning for paid x402 enrichment.

Decides WHEN a paid refresh is worth $0.01–0.03 (hot candidates, real
positions) and WHICH symbols to enrich (top-N by cheap rank). All checks run
on the FREE keyless snapshot and are side-effect-free mirrors of the breakout
engine's two cheap core gates (volume breakout, recent-high break) — they
must never write to the engine's price/volume LocalCaches.

The full target universe stays visible every cycle through the free keyless
path; this module only narrows what the paid x402 layer refreshes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings
from src.config.tokens import is_momentum_candidate_symbol, is_tradable_symbol

LOGGER = logging.getLogger(__name__)

CHEAP_CORE_GATE_COUNT = 2


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def cheap_gate_pass_count(token_data: dict[str, Any], settings: Settings) -> int:
    """Stateless approximation of the engine's two cheap core gates."""

    passes = 0
    price = _positive_number(token_data.get("price"))
    volume_1h = _positive_number(token_data.get("volume_1h"))
    volume_24h = _positive_number(token_data.get("volume_24h"))
    market_cap = _positive_number(token_data.get("market_cap"))
    rolling_avg = _positive_number(token_data.get("rolling_24h_hourly_volume_avg"))
    if rolling_avg is None and volume_24h is not None:
        rolling_avg = volume_24h / 24.0

    # Gate 1: volume breakout.
    breakout_mult = float(getattr(settings, "ml_volume_breakout_multiplier", 2.0))
    if volume_1h is not None and rolling_avg is not None and rolling_avg > 0:
        if volume_1h > breakout_mult * rolling_avg:
            passes += 1
    elif volume_24h is not None and market_cap is not None and volume_24h > 0.05 * market_cap:
        passes += 1

    # Gate 2: recent-high break (engine falls back to high_3h/high_6h when
    # its price cache is cold, which is exactly what we mirror here).
    reference_high = _positive_number(token_data.get("high_3h"))
    if reference_high is None:
        reference_high = _positive_number(token_data.get("high_6h"))
    buffer_multiplier = 1.0 + float(getattr(settings, "breakout_buffer", 0.002))
    if price is not None and reference_high is not None and price > reference_high * buffer_multiplier:
        passes += 1

    return passes


def hot_candidate_symbols(
    snapshot: dict[str, dict[str, Any]],
    settings: Settings,
) -> list[str]:
    """Symbols currently passing BOTH cheap core gates (refresh-worthy)."""

    hot: list[str] = []
    for symbol, token_data in snapshot.items():
        if not isinstance(token_data, dict):
            continue
        normalized = str(symbol).upper()
        if not is_tradable_symbol(normalized) or not is_momentum_candidate_symbol(normalized):
            continue
        if cheap_gate_pass_count(token_data, settings) >= CHEAP_CORE_GATE_COUNT:
            hot.append(normalized)
    return sorted(hot)


def select_enrichment_symbols(
    keyless_snapshot: dict[str, dict[str, Any]],
    target_symbols: list[str],
    position_symbols: set[str],
    settings: Settings,
    top_n: int | None = None,
) -> list[str]:
    """Pick which symbols a paid refresh should enrich.

    Top-N targets by cheap rank (gates passed, 1h volume surge, 24h volume),
    always including open-position symbols and BNB (regime reference). With
    N <= 0 or no keyless data to rank on, fall back to the full target list.

    ``top_n`` overrides the ``x402_enrich_top_n`` setting when provided,
    e.g. from the optimizer's ``compute_optimal_n``.
    """

    top_n = int(top_n if top_n is not None else (getattr(settings, "x402_enrich_top_n", 0) or 0))
    targets = [str(symbol).upper() for symbol in target_symbols]
    # Exclude stablecoins and momentum-excluded from ranking; positions are
    # added back via must_have regardless of tradability.
    targets = [s for s in targets if is_tradable_symbol(s) and is_momentum_candidate_symbol(s)]
    if top_n <= 0 or top_n >= len(targets) or not keyless_snapshot:
        return targets

    def _rank(symbol: str) -> tuple[int, float, float]:
        token_data = keyless_snapshot.get(symbol)
        if not isinstance(token_data, dict):
            return (0, 0.0, 0.0)
        volume_1h = _positive_number(token_data.get("volume_1h")) or 0.0
        volume_24h = _positive_number(token_data.get("volume_24h")) or 0.0
        rolling_avg = _positive_number(token_data.get("rolling_24h_hourly_volume_avg"))
        if rolling_avg is None and volume_24h:
            rolling_avg = volume_24h / 24.0
        surge = volume_1h / rolling_avg if rolling_avg else 0.0
        return (cheap_gate_pass_count(token_data, settings), surge, volume_24h)

    ranked = sorted(targets, key=_rank, reverse=True)
    selected = ranked[:top_n]
    must_have = {symbol.upper() for symbol in position_symbols}
    must_have.add("BNB")
    chosen = set(selected)
    for symbol in sorted(must_have):
        if symbol in chosen:
            continue
        selected.append(symbol)
        chosen.add(symbol)
    LOGGER.info(
        "Paid enrichment scope: %d/%d symbols (top_n=%d, positions=%s)",
        len(selected),
        len(targets),
        top_n,
        sorted(must_have - {"BNB"}) or "none",
    )
    return selected
