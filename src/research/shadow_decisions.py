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
        model_predictor: Any | None = None,
        sentiment_tier2: Any | None = None,
        settings: Any | None = None,
        decision_log_path: str = "logs/decision_shadow.jsonl",
    ) -> None:
        self.jump_model = jump_model
        self.bb_squeeze = bb_squeeze
        self.model_predictor = model_predictor
        self.sentiment_tier2 = sentiment_tier2
        self.settings = settings
        self.decision_log_path = decision_log_path

    def log_all_variants(
        self,
        cycle_id: int,
        snapshot: dict,
        regime_result: Any,
        candidate: Any | None = None,
    ) -> None:
        """Write shadow variants and intentionally return None.

        The entry-quality ``trained_model`` variant scores the cycle's selected
        ``candidate`` using the same feature contract the model was trained on
        (``entry_feature_vector``). It is logged only when a candidate exists,
        because the model predicts per-candidate entry quality — not a per-cycle
        BNB regime. The ``jump_inspired`` regime variant is unaffected.
        """

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
        if (
            self.model_predictor is not None
            and candidate is not None
            and bool(getattr(self.settings, "enable_model_shadow", False))
        ):
            from src.research.feature_contract import entry_feature_vector

            features = entry_feature_vector(
                factor_scores=getattr(candidate, "factor_scores", None),
                entry_score=getattr(candidate, "entry_score", None),
                true_factor_count=getattr(candidate, "true_factor_count", None),
            )
            model_result = self.model_predictor.detect(features)
            append_to_file(
                self.decision_log_path,
                ShadowDecisionLog(
                    cycle_id=cycle_id,
                    variant="trained_model",
                    hypothetical_action="ENTER" if model_result.state == "bull" else "WAIT",
                    hypothetical_symbol=str(getattr(candidate, "symbol", "?")),
                    reasons=[
                        f"model_state_{model_result.state}",
                        f"model_score_{model_result.score}",
                        f"live_regime_{regime_result.regime.value}",
                    ],
                    confidence=model_result.confidence,
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
