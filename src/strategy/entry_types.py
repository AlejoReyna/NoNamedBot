"""Shared entry candidate types for strategy evaluators."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class EntryCandidate:
    """Normalized entry candidate selected by a strategy evaluator."""

    symbol: str
    price: float
    position_size_usdc: float
    expected_amount_out: Decimal
    slippage_small: float | None
    slippage_normal: float | None
    reason: str
    factor_scores: dict[str, bool]
    true_factor_count: int
    source: str = "scoring_v25"
    entry_score: float | None = None
    position_size_multiplier: float = 1.0
    strategy_mode: str = "breakout"
    # Human-readable measured value behind each factor, carried through to telemetry.
    factor_metrics: dict[str, str] = field(default_factory=dict)
    # ML audit payload attached by evaluators that consume an ML bundle.
    ml_audit: dict[str, Any] | None = None
    # Quality-guard results and the first blocking reason for dashboard audit.
    quality_guards: dict[str, bool] | None = None
    entries_blocked_reason: str | None = None

    def with_ml_audit(self, ml_audit: dict[str, Any] | None) -> "EntryCandidate":
        """Return a copy with the ML audit payload replaced."""

        if ml_audit is None:
            return self
        return EntryCandidate(
            symbol=self.symbol,
            price=self.price,
            position_size_usdc=self.position_size_usdc,
            expected_amount_out=self.expected_amount_out,
            slippage_small=self.slippage_small,
            slippage_normal=self.slippage_normal,
            reason=self.reason,
            factor_scores=dict(self.factor_scores),
            true_factor_count=self.true_factor_count,
            source=self.source,
            entry_score=self.entry_score,
            position_size_multiplier=self.position_size_multiplier,
            strategy_mode=self.strategy_mode,
            factor_metrics=dict(self.factor_metrics),
            ml_audit=ml_audit,
            quality_guards=self.quality_guards,
            entries_blocked_reason=self.entries_blocked_reason,
        )
