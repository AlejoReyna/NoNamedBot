"""Breakout strategy universe evaluator."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from src.config.settings import Settings
from src.execution.twak_interface import TWAKInterface
from src.ml.types import MLContext, ranking_audit_to_dict
from src.strategy.candidate_adapter import breakout_decision_to_candidate

_fallback = importlib.import_module("src.strategy.6falgorithm.fallback_scorer")
fallback_scoring_evaluate_universe = _fallback.fallback_scoring_evaluate_universe
from src.strategy.entry_types import EntryCandidate
from src.strategy.guardrails import RiskDecision
from src.strategy.regime_detector import RegimeResult

_breakout_module = importlib.import_module("src.strategy.6falgorithm.breakout_engine")
BreakoutEngine = _breakout_module.BreakoutEngine

LOGGER = logging.getLogger(__name__)


def evaluate_universe_breakout(
    snapshot: dict[str, dict[str, Any]],
    portfolio_value: float,
    regime_result: RegimeResult,
    risk_decision: RiskDecision,
    *,
    settings: Settings,
    twak_interface: TWAKInterface | None = None,
    exclude_symbols: set[str] | None = None,
    use_breakout_engine: bool = True,
    ml_bundle: Any | None = None,
) -> EntryCandidate | None:
    """Evaluate the universe using the 6-factor BreakoutEngine or legacy fallback."""

    if not use_breakout_engine:
        return fallback_scoring_evaluate_universe(
            snapshot,
            portfolio_value,
            regime_result,
            risk_decision,
            settings=settings,
            twak_interface=twak_interface,
            exclude_symbols=exclude_symbols,
        )

    engine = BreakoutEngine(settings, twak_interface)
    filtered_snapshot = {
        symbol: data
        for symbol, data in snapshot.items()
        if symbol.upper() not in {item.upper() for item in (exclude_symbols or set())}
    }

    ml_contexts: dict[str, MLContext] = {}
    ranking_audit: Any | None = None
    if ml_bundle is not None:
        try:
            ml_bundle.refresh_ohlcv_if_stale()
            ml_contexts = ml_bundle.build_contexts(filtered_snapshot)
        except Exception as exc:
            LOGGER.warning("ML bundle context build failed; falling back to rule-only ranking: %s", exc)
            ml_contexts = {}

    decisions = engine.evaluate_all(filtered_snapshot, portfolio_value, ml_contexts=ml_contexts)
    passers = [decision for decision in decisions if decision.should_enter]
    selected: Any | None = None
    if not passers:
        return None

    if (
        ml_bundle is not None
        and len(passers) > 1
        and ml_bundle.is_ranking_active
    ):
        from src.ml.candidate_ranker import CandidateRanker

        selected, ranking_audit = CandidateRanker().rank(passers, ml_contexts)

    if selected is None:
        selected = passers[0]

    candidate = breakout_decision_to_candidate(
        selected,
        snapshot,
        portfolio_value,
        settings,
        risk_decision,
    )
    if candidate is None:
        return None

    ml_context = getattr(selected, "ml_context", None)
    candidate = candidate.with_ml_audit(
        _build_ml_audit(
            ml_bundle=ml_bundle,
            ml_context=ml_context,
            selected=selected,
            passers=passers,
            ranking_audit=ranking_audit,
        )
    )
    return candidate


def _build_ml_audit(
    ml_bundle: Any | None,
    ml_context: MLContext | None,
    selected: Any,
    passers: list[Any],
    ranking_audit: Any | None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "ml_enabled": ml_bundle is not None,
        "ml_active": bool(ml_bundle is not None and getattr(ml_bundle, "is_ranking_active", False)),
        "ml_shadow_mode": bool(getattr(ml_bundle, "settings", None) and getattr(ml_bundle.settings, "ml_shadow_mode", True)),
        "ml_validation_auc": float(getattr(ml_bundle, "validation_auc", 0.0) or 0.0),
    }
    if ml_context is not None:
        audit["ml_regime"] = ml_context.regime
        audit["ml_confidence"] = round(ml_context.confidence, 6)
        audit["ml_position_size_multiplier"] = ml_context.position_size_multiplier
    else:
        audit["ml_regime"] = None
        audit["ml_confidence"] = None
        audit["ml_position_size_multiplier"] = None
    if ranking_audit is not None:
        audit["ml_ranking_audit"] = ranking_audit_to_dict(ranking_audit)
    else:
        audit["ml_ranking_audit"] = None
    if passers:
        audit["ml_passer_count"] = len(passers)
        audit["ml_passer_symbols"] = [(getattr(decision, "symbol", None) or "").upper() for decision in passers]
    else:
        audit["ml_passer_count"] = 0
        audit["ml_passer_symbols"] = []

    # Surface quality-guard state so the dashboard can show why a candidate was blocked.
    audit["quality_guards"] = dict(getattr(selected, "quality_guards", {}) or {})
    audit["entries_blocked_reason"] = getattr(selected, "entries_blocked_reason", None)
    return audit
