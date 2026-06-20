"""Deployment runtime helpers for live trading."""

from __future__ import annotations

import logging
from typing import Any

from src.config.eligible_tokens import assert_tradable_subset_of_eligible
from src.config.settings import Settings
from src.deployment.alerts import check_disk_guard
from src.deployment.health_server import start_health_server
from src.deployment.health_state import HealthState
from src.deployment.reconciliation import load_pending_swap_cooldowns, reconcile_positions_on_startup
from src.deployment.twak_unlock import verify_twak_unlock

LOGGER = logging.getLogger(__name__)


def deployment_startup(
    settings: Settings,
    *,
    position_manager: Any,
    toolkit: Any,
) -> tuple[HealthState | None, Any, set[str]]:
    """
    Run live deployment checks and services.

    Returns (health_state, health_server, pending_swap_cooldown).
    """

    pending_cooldowns: set[str] = set()
    if not settings.paper_trade:
        assert_tradable_subset_of_eligible()
        unlock = verify_twak_unlock()
        if not unlock["ok"]:
            raise RuntimeError(f"TWAK unlock failed: {unlock['detail']}")
        LOGGER.info("TWAK wallet unlocked: %s", unlock.get("address"))
        removed = reconcile_positions_on_startup(position_manager, toolkit)
        if removed:
            LOGGER.warning("Startup reconciliation removed positions: %s", removed)
        pending_cooldowns = load_pending_swap_cooldowns(settings.execution_log_path)

    health_state: HealthState | None = None
    health_server = None
    port = int(getattr(settings, "health_check_port", 0) or 0)
    if port > 0:
        health_state = HealthState()
        health_state.update(status="ok")
        health_server = start_health_server(
            health_state,
            port=port,
            decision_log_path=settings.decision_log_path,
        )
    return health_state, health_server, pending_cooldowns


def disk_allows_entries(settings: Settings) -> bool:
    return check_disk_guard(
        min_free_bytes=int(getattr(settings, "disk_guard_min_free_bytes", 500_000_000)),
        telegram_token=getattr(settings, "telegram_bot_token", None),
        telegram_chat_id=getattr(settings, "telegram_chat_id", None),
    )


def update_health_snapshot(
    health_state: HealthState | None,
    *,
    guardrails: Any,
    portfolio_value: float,
    position_manager: Any,
    settings: Settings | None = None,
) -> None:
    if health_state is None:
        return
    ath = float(getattr(guardrails, "portfolio_ath", portfolio_value) or portfolio_value)
    drawdown = 0.0
    if ath > 0:
        drawdown = max(0.0, (ath - portfolio_value) / ath * 100.0)
    
    # Fetch x402 wallet balance if settings available
    x402_address: str | None = None
    x402_balance: float | None = None
    if settings is not None:
        try:
            from src.data.x402_wallet_view import fetch_x402_wallet_view
            view = fetch_x402_wallet_view(base_rpc_url=settings.base_rpc_url)
            x402_address = view.address
            if view.usdc_balance is not None:
                x402_balance = float(view.usdc_balance)
        except Exception as exc:
            LOGGER.debug("x402 wallet snapshot failed: %s", exc)
    
    health_state.update(
        last_cycle_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        positions=len(position_manager.list_open_positions()),
        daily_trades=int(getattr(guardrails, "daily_trade_count", 0)),
        drawdown_pct=drawdown,
        status="ok",
        x402_wallet_address=x402_address,
        x402_usdc_balance=x402_balance,
    )
