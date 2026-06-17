"""Append-only strategy decision audit log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.config.settings import Settings

DecisionAction = Literal["ENTER", "WAIT", "BLOCKED", "HALT"]


class DecisionLogger:
    """Write strategy decision records as JSON Lines."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log(
        self,
        *,
        cycle_number: int,
        mode: str,
        portfolio_value_usdc: float,
        position_count: int,
        entries_allowed: bool,
        action: DecisionAction,
        reason: str,
        priced_target_count: int,
        symbol: str | None = None,
        position_size_usdc: float = 0.0,
        factor_scores: dict[str, bool] | None = None,
        true_factor_count: int = 0,
        estimated_slippage_pct: float | None = None,
        strategy_mode: str | None = None,
        entry_score: float | None = None,
        entries_blocked_reason: str | None = None,
        exit_reason: str | None = None,
        hold_time_seconds: int | None = None,
        factor_metrics: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Append one strategy decision record and return it."""

        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle_number": cycle_number,
            "mode": mode,
            "portfolio_value_usdc": portfolio_value_usdc,
            "position_count": position_count,
            "entries_allowed": entries_allowed,
            "action": action,
            "symbol": symbol.upper() if symbol else None,
            "position_size_usdc": position_size_usdc,
            "factor_scores": factor_scores or {},
            "factor_metrics": factor_metrics or {},
            "true_factor_count": true_factor_count,
            "estimated_slippage_pct": estimated_slippage_pct,
            "reason": reason,
            "priced_target_count": priced_target_count,
        }
        if strategy_mode is not None:
            record["strategy_mode"] = strategy_mode
        if entry_score is not None:
            record["entry_score"] = entry_score
        if entries_blocked_reason is not None:
            record["entries_blocked_reason"] = entries_blocked_reason
        if exit_reason is not None:
            record["exit_reason"] = exit_reason
        if hold_time_seconds is not None:
            record["hold_time_seconds"] = hold_time_seconds

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
        return record


def log_decision(
    settings: Settings,
    *,
    cycle_number: int,
    portfolio_value_usdc: float,
    position_count: int,
    entries_allowed: bool,
    action: DecisionAction,
    reason: str,
    priced_target_count: int,
    symbol: str | None = None,
    position_size_usdc: float = 0.0,
    factor_scores: dict[str, bool] | None = None,
    true_factor_count: int = 0,
    estimated_slippage_pct: float | None = None,
    strategy_mode: str | None = None,
    entry_score: float | None = None,
    entries_blocked_reason: str | None = None,
    exit_reason: str | None = None,
    hold_time_seconds: int | None = None,
    factor_metrics: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Append a strategy decision record using the configured settings path."""

    mode = "paper" if settings.paper_trade else "live"
    return DecisionLogger(settings.decision_log_path).log(
        cycle_number=cycle_number,
        mode=mode,
        portfolio_value_usdc=portfolio_value_usdc,
        position_count=position_count,
        entries_allowed=entries_allowed,
        action=action,
        reason=reason,
        priced_target_count=priced_target_count,
        symbol=symbol,
        position_size_usdc=position_size_usdc,
        factor_scores=factor_scores,
        true_factor_count=true_factor_count,
        estimated_slippage_pct=estimated_slippage_pct,
        strategy_mode=strategy_mode,
        entry_score=entry_score,
        entries_blocked_reason=entries_blocked_reason,
        exit_reason=exit_reason,
        hold_time_seconds=hold_time_seconds,
        factor_metrics=factor_metrics,
    )
