"""Tests for swap-router slippage protection."""

from __future__ import annotations

import pytest

from src.execution.swap_router import PancakeSwapRouter


class FakeTWAK:
    """Fake TWAK interface returning a configurable swap result."""

    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, str, float, float]] = []

    def swap(self, from_symbol: str, to_symbol: str, amount: float, slippage_pct: float) -> dict[str, object]:
        self.calls.append((from_symbol, to_symbol, amount, slippage_pct))
        return self.result


def test_router_rejects_slippage_cap_over_one_percent() -> None:
    router = PancakeSwapRouter(FakeTWAK({"amount_out": 100.0}))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        router.swap_exact_in("USDC", "CAKE", 100.0, 0.011)


def test_router_rejects_missing_execution_slippage() -> None:
    router = PancakeSwapRouter(FakeTWAK({"amount_out": 100.0}))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        router.swap_exact_in("USDC", "CAKE", 100.0, 0.0)


def test_router_aborts_when_output_is_below_one_percent_floor() -> None:
    router = PancakeSwapRouter(
        FakeTWAK({"expected_amount_out": 100.0, "amount_out": 98.9})  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError):
        router.swap_exact_in("USDC", "CAKE", 100.0, 0.01)


def test_router_accepts_output_at_one_percent_floor() -> None:
    twak = FakeTWAK({"expected_amount_out": 100.0, "amount_out": 99.0})
    router = PancakeSwapRouter(
        twak  # type: ignore[arg-type]
    )

    result = router.swap_exact_in("USDC", "CAKE", 100.0, 0.01)
    assert result["amount_out"] == 99.0
    assert twak.calls == [("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 100.0, 0.01)]
