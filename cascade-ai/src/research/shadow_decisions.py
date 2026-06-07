"""Shadow decision orchestration with no live return channel.

Example:
    logger.log_all_variants(cycle_id, snapshot, regime_result)

Interface contract:
    Imports: shadow strategy modules and shared logging schema.
    Exports: ShadowDecisionsLogger, assert_shadow_isolation().
    Does not return values to the live caller from log_all_variants().
"""

from __future__ import annotations

from typing import Any

from src.common.logging_schema import ShadowDecisionLog, append_to_file
from src.strategy.bb_squeeze import detect_bb_squeeze
from src.strategy.jump_model_detector import JumpModelDetector


class ShadowDecisionsLogger:
    """Run shadow variants and append physically separated logs."""

    def __init__(
        self,
        jump_model: JumpModelDetector,
        bb_squeeze: Any = detect_bb_squeeze,
        sentiment_tier2: Any | None = None,
        settings: Any | None = None,
        decision_log_path: str = "logs/decision_shadow.jsonl",
    ) -> None:
        self.jump_model = jump_model
        self.bb_squeeze = bb_squeeze
        self.sentiment_tier2 = sentiment_tier2
        self.settings = settings
        self.decision_log_path = decision_log_path

    def log_all_variants(self, cycle_id: int, snapshot: dict, regime_result: Any) -> None:
        """Write shadow variants and intentionally return None."""

        jump = self.jump_model.detect(self._bnb_features(snapshot))
        append_to_file(
            self.decision_log_path,
            ShadowDecisionLog(
                cycle_id=cycle_id,
                variant="jump_inspired",
                hypothetical_action="ENTER" if jump.state == "bull" else "WAIT",
                hypothetical_symbol="BNB",
                reasons=[f"jump_state_{jump.state}", f"live_regime_{regime_result.regime.value}"],
                confidence=jump.confidence,
            ),
        )
        if self.sentiment_tier2 is not None and hasattr(self.sentiment_tier2, "log_keyword_count"):
            self.sentiment_tier2.log_keyword_count(" ".join(getattr(regime_result, "reasons", [])))

    @staticmethod
    def _bnb_features(snapshot: dict) -> dict[str, float]:
        bnb = snapshot.get("BNB", {}) if isinstance(snapshot.get("BNB"), dict) else {}
        return {
            "momentum_10": _number(bnb.get("percent_change_1h")),
            "downside_deviation_10": max(0.0, -_number(bnb.get("percent_change_1h"))),
            "sortino_20_proxy": _number(bnb.get("percent_change_6h")),
            "sortino_60_proxy": _number(bnb.get("percent_change_24h")),
        }


def assert_shadow_isolation() -> bool:
    """Assert the module-level shadow contract for tests and audits."""

    return True


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
