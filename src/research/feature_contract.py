"""Canonical, dependency-free entry-time feature contract.

This module is the single source of truth for the model's feature names. It is
deliberately free of pandas/sklearn so the live trading box can build shadow
features without the offline ML dependencies installed. The offline dataset
builder (``dataset.py``) and the live shadow predictor both build features
through ``entry_feature_vector`` so training and serving feature names always
match — eliminating train/serve skew.

CMC quote columns are intentionally excluded: the offline as-of join uses the
collector DB schema, which the live snapshot does not reproduce 1:1. Re-add them
only once a single shared snapshot->feature builder feeds both paths.
"""

from __future__ import annotations

from typing import Any

# Reproducible entry-time scalars present identically at training time (from the
# trade-outcome entry event) and at serving time (from the live EntryCandidate).
REPRODUCIBLE_SCALAR_FEATURES = {
    "entry_score",
    "true_factor_count",
    # Volatility / execution context logged at entry time
    "estimated_slippage_pct",   # already in entry log; whitelisted here
    "atr_pct",                  # 14-period ATR as % of price at entry
    # Market regime at entry (one-hot; RANGING is the base/omitted class)
    "regime_trending_up",
    "regime_risk_off",
    # BNB macro momentum at entry — drives BSC market tailwind/headwind
    "bnb_1h_pct",
    "bnb_24h_pct",
}
FACTOR_PREFIX = "factor_"


def to_float(value: Any) -> float:
    """Best-effort float coercion; None/garbage becomes 0.0."""

    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def entry_feature_vector(
    *,
    factor_scores: dict[str, Any] | None,
    entry_score: Any = None,
    true_factor_count: Any = None,
    estimated_slippage_pct: Any = None,
    atr_pct: Any = None,
    regime: Any = None,
    bnb_1h_pct: Any = None,
    bnb_24h_pct: Any = None,
) -> dict[str, float]:
    """Canonical model feature dict shared by training and shadow serving.

    Produces ``factor_<key>`` indicators plus the reproducible entry-time
    scalars. Both ``build_dataset`` (offline) and the shadow predictor (live)
    build features through this single contract so feature names always match.

    New context parameters (all optional / default 0.0 when absent so old
    callers remain valid and old log rows train without error):
      estimated_slippage_pct — execution cost proxy; already in the entry log.
      atr_pct               — 14-period ATR as % of price; logged from entry path.
      regime                — MarketRegime value or its .value string; one-hotted.
      bnb_1h_pct / bnb_24h_pct — BNB macro momentum from the market snapshot.
    """

    features: dict[str, float] = {}
    for key, value in (factor_scores or {}).items():
        features[f"{FACTOR_PREFIX}{key}"] = float(int(bool(value)))
    features["entry_score"] = to_float(entry_score)
    features["true_factor_count"] = to_float(true_factor_count)
    features["estimated_slippage_pct"] = to_float(estimated_slippage_pct)
    features["atr_pct"] = to_float(atr_pct)
    # Regime one-hot: RANGING is the omitted base class.
    regime_str = str(getattr(regime, "value", regime) or "").lower()
    features["regime_trending_up"] = 1.0 if "trending_up" in regime_str else 0.0
    features["regime_risk_off"] = 1.0 if "risk_off" in regime_str else 0.0
    features["bnb_1h_pct"] = to_float(bnb_1h_pct)
    features["bnb_24h_pct"] = to_float(bnb_24h_pct)
    return features


def is_model_feature(column: str) -> bool:
    """True only for reproducible, entry-time-safe model features."""

    return column.startswith(FACTOR_PREFIX) or column in REPRODUCIBLE_SCALAR_FEATURES
