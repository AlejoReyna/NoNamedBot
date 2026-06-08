"""PancakeSwap V3 conceptual routing through TWAK swap execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.execution.twak_interface import TWAKInterface


@dataclass(frozen=True)
class SwapQuote:
    """Simple swap quote used by the strategy and guardrails."""

    from_symbol: str
    to_symbol: str
    amount_in: float
    estimated_amount_out: float
    estimated_slippage_pct: float


class PancakeSwapRouter:
    """Route swaps through TWAK without importing PancakeSwap SDKs."""

    HARD_SLIPPAGE_CAP_PCT = 0.01

    def __init__(self, twak_interface: TWAKInterface) -> None:
        self.twak_interface = twak_interface

    def estimate_slippage(
        self,
        from_symbol: str,
        to_symbol: str,
        amount: float,
        market_data: dict[str, Any],
    ) -> float:
        """Estimate slippage from normalized market data when available."""

        raw_value = market_data.get("estimated_slippage_pct", 0.0)
        try:
            return max(0.0, float(raw_value))
        except (TypeError, ValueError):
            return 0.0

    def quote_exact_in(
        self,
        from_symbol: str,
        to_symbol: str,
        amount: float,
        market_data: dict[str, Any],
    ) -> SwapQuote:
        """Build a deterministic quote skeleton from normalized market data."""

        slippage = self.estimate_slippage(from_symbol, to_symbol, amount, market_data)
        return SwapQuote(
            from_symbol=from_symbol.upper(),
            to_symbol=to_symbol.upper(),
            amount_in=amount,
            estimated_amount_out=amount * (1 - slippage),
            estimated_slippage_pct=slippage,
        )

    def swap_exact_in(
        self,
        from_symbol: str,
        to_symbol: str,
        amount: float,
        max_slippage_pct: float,
        expected_amount_out: float | None = None,
    ) -> dict[str, Any]:
        """Execute an exact-input swap through TWAK."""

        if max_slippage_pct <= 0:
            raise ValueError("swap slippage must be greater than zero")
        if max_slippage_pct > self.HARD_SLIPPAGE_CAP_PCT:
            raise ValueError("swap slippage cap cannot exceed 1%")

        from src.config.tokens import resolve_twak_token

        from_arg = resolve_twak_token(from_symbol)
        to_arg = resolve_twak_token(to_symbol)

        result = self.twak_interface.swap(from_arg, to_arg, amount, max_slippage_pct)
        self._enforce_output_floor(result, amount, max_slippage_pct, expected_amount_out)
        return result

    def _enforce_output_floor(
        self,
        result: dict[str, Any],
        amount: float,
        max_slippage_pct: float,
        expected_amount_out: float | None,
    ) -> None:
        expected = expected_amount_out
        if expected is None:
            expected = self._first_number(
                result,
                ("expected_amount_out", "expected_output", "quoted_amount_out", "quote_amount_out"),
            )
        actual = self._first_number(
            result,
            ("amount_out", "estimated_amount_out", "received_amount", "to_amount"),
        )
        if expected is None or actual is None:
            return

        output_floor = expected * (1 - max_slippage_pct)
        if actual < output_floor:
            raise ValueError(
                f"swap output {actual:.8f} is below protected floor {output_floor:.8f}"
            )

    @staticmethod
    def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = payload.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None
