"""Shadow-only jump-inspired regime classifier.

Example:
    result = JumpModelDetector(price_cache).detect(features)

Interface contract:
    Imports: standard library dataclasses only.
    Exports: JumpModelResult, JumpModelDetector.
    Does not affect live decisions, sizing, execution, or guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategy.volatility import PriceCache


@dataclass(frozen=True)
class JumpModelResult:
    """Shadow classifier output for offline comparison."""

    state: str
    confidence: float
    score: int
    features: dict[str, float]
    source: str = "SHADOW"


class JumpModelDetector:
    """Jump-inspired threshold classifier with persistence."""

    def __init__(self, price_cache: PriceCache) -> None:
        self.price_cache = price_cache
        self._previous_state: str | None = None
        self._previous_confidence = 0.0

    def detect(self, bnb_features: dict[str, Any]) -> JumpModelResult:
        """Classify bull/bear from deterministic thresholds."""

        features = {key: self._number(value) for key, value in bnb_features.items()}
        score = 0
        if features.get("momentum_10", 0.0) > 0:
            score += 1
        if features.get("downside_deviation_10", 1.0) < 0.03:
            score += 1
        if features.get("sortino_20_proxy", 0.0) > 0:
            score += 1
        if features.get("sortino_60_proxy", 0.0) > 0:
            score += 1

        new_state = "bull" if score >= 3 else "bear"
        confidence = score / 4 if new_state == "bull" else (4 - score) / 4
        if self._previous_state and new_state != self._previous_state and confidence < 0.75:
            new_state = self._previous_state
            confidence = min(self._previous_confidence, 0.74)

        self._previous_state = new_state
        self._previous_confidence = confidence
        return JumpModelResult(new_state, confidence, score, features)

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
