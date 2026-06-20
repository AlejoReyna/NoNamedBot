"""Configuration helpers for optional market data adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

CMC_MCP_URL = "https://mcp.coinmarketcap.com/x402/mcp"


@dataclass(frozen=True)
class DataSourceConfig:
    """Environment-backed settings for the optional CMC MCP/x402 adapter."""

    cmc_mcp_enabled: bool = False
    cmc_mcp_shadow_mode: bool = True
    cmc_mcp_url: str = CMC_MCP_URL
    cmc_x402_chain_id: int = 8453
    cmc_x402_max_usdc_per_call: Decimal = Decimal("0.015")


def load_data_source_config() -> DataSourceConfig:
    """Load optional data-source configuration from process environment."""

    return DataSourceConfig(
        cmc_mcp_enabled=_get_bool("CMC_MCP_ENABLED", False),
        cmc_mcp_shadow_mode=_get_bool("CMC_MCP_SHADOW_MODE", True),
        cmc_mcp_url=os.getenv("CMC_MCP_URL", CMC_MCP_URL),
        cmc_x402_chain_id=_get_int("CMC_X402_CHAIN_ID", 8453),
        cmc_x402_max_usdc_per_call=_get_decimal("CMC_X402_MAX_USDC_PER_CALL", Decimal("0.015")),
    )


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value, 0)
    except ValueError:
        return default


def _get_decimal(name: str, default: Decimal) -> Decimal:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return Decimal(value)
    except Exception:
        return default

