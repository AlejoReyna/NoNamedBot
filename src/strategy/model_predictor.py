"""Fail-closed trained-model shadow predictor."""

from __future__ import annotations

import logging
from typing import Any

from src.strategy.jump_model_detector import JumpModelResult

LOGGER = logging.getLogger(__name__)


class ModelPredictor:
    """Adapter that exposes trained model artifacts through the jump contract."""

    def __init__(self, artifact_path: str, threshold: float = 0.55) -> None:
        self.artifact_path = artifact_path
        self.threshold = threshold
        self.artifact: Any | None = None
        self.available = False
        try:
            from src.ml.model_store import load_artifact

            self.artifact = load_artifact(artifact_path)
            self.available = True
        except Exception as exc:
            LOGGER.warning("ModelPredictor disabled: %s", exc)

    def detect(self, features: dict[str, Any]) -> JumpModelResult:
        numeric = {key: self._number(value) for key, value in features.items()}
        if not self.available or self.artifact is None:
            return JumpModelResult("bear", 0.0, 0, numeric, source="SHADOW")
        try:
            ordered = [numeric.get(name, 0.0) for name in self.artifact.feature_names]
            confidence = self._predict_confidence(ordered)
            state = "bull" if confidence >= self.threshold else "bear"
            score = int(round(confidence * 100))
            return JumpModelResult(state, confidence, score, numeric, source="SHADOW")
        except Exception as exc:
            LOGGER.warning("ModelPredictor.detect failed: %s", exc)
            return JumpModelResult("bear", 0.0, 0, numeric, source="SHADOW")

    def _predict_confidence(self, ordered: list[float]) -> float:
        model = self.artifact.model
        if hasattr(model, "predict_proba"):
            probability = model.predict_proba([ordered])[0][1]
        else:
            probability = model.predict([ordered])[0]
        return max(0.0, min(1.0, float(probability)))

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
