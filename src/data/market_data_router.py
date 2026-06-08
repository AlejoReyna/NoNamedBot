"""Market data source router with fail-open CMC MCP shadow support."""

from __future__ import annotations

import inspect
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class MarketDataRouter:
    """Route quote requests between optional CMC MCP and the Keyless fallback.

    The MCP/x402 branch is data-fetching only. It must not be used for swap
    execution or pass any data-payment signing material into src.execution.
    """

    def __init__(
        self,
        keyless_client: Any,
        mcp_client: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.keyless_client = keyless_client
        self.mcp_client = mcp_client
        self.logger = logger or LOGGER

    async def get_quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict[str, Any]:
        """Return latest CMC quotes, falling back to Keyless on any MCP/x402 issue."""

        if self._mcp_enabled() and not self._mcp_shadow_mode():
            try:
                return await self._call_client(self.mcp_client, symbols, convert)
            except Exception as exc:
                self.logger.warning("[CMC_MCP_FALLBACK] MCP quote failed; using Keyless: %s", exc)
        return await self._call_client(self.keyless_client, symbols, convert)

    async def shadow_check_mcp(self, symbols: list[str], convert: str = "USD") -> None:
        """Exercise MCP in shadow mode without affecting trading data."""

        if not self._mcp_enabled():
            return
        try:
            await self._call_client(self.mcp_client, symbols, convert)
            self.logger.info("[CMC_MCP_SHADOW] MCP quote check succeeded")
        except Exception as exc:
            self.logger.warning("[CMC_MCP_SHADOW] MCP quote check failed: %s", exc)

    async def _call_client(self, client: Any, symbols: list[str], convert: str) -> dict[str, Any]:
        if client is None:
            raise RuntimeError("market data client is not configured")
        method = self._quote_method(client)
        kwargs = {"convert": convert} if _accepts_kwarg(method, "convert") else {}
        result = method(symbols, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise RuntimeError("market data client returned a non-dict payload")
        return result

    @staticmethod
    def _quote_method(client: Any) -> Any:
        method = getattr(client, "get_quotes_latest", None)
        if callable(method):
            return method
        method = getattr(client, "get_crypto_quotes_latest", None)
        if callable(method):
            return method
        raise RuntimeError("market data client has no latest-quotes method")

    def _mcp_enabled(self) -> bool:
        return bool(self.mcp_client and getattr(self.mcp_client, "enabled", False))

    def _mcp_shadow_mode(self) -> bool:
        return bool(getattr(self.mcp_client, "shadow_mode", True))


def _accepts_kwarg(method: Any, name: str) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return name in signature.parameters
