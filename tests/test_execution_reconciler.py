"""Tests for execution reconciliation."""

from __future__ import annotations

from decimal import Decimal

from src.execution.execution_reconciler import ExecutionReconciler


class FakeBalances:
    def __init__(self, balances: dict[str, Decimal]) -> None:
        self.balances = balances

    def get_balance(self, symbol: str) -> dict[str, object]:
        return {"balances": {symbol: self.balances.get(symbol, Decimal("0"))}}


def test_reconcile_fails_on_receipt_status_0() -> None:
    tx = {"hash": "0xabc", "status": 0, "receipt": {"status": 0}}
    result = ExecutionReconciler(FakeBalances({})).reconcile(tx, Decimal("100"), Decimal("0.01"), {})
    assert result.status == "FAILED"


def test_reconcile_slippage_exceeded_when_actual_low() -> None:
    tx = {"hash": "0xabc", "status": 1, "receipt": {"status": 1, "gasUsed": 100000, "blockNumber": 123}}
    balance_before = {"CAKE": Decimal("0")}
    reconciler = ExecutionReconciler(FakeBalances({"CAKE": Decimal("95")}))
    result = reconciler.reconcile(tx, Decimal("100"), Decimal("0.01"), balance_before)
    assert result.status == "SLIPPAGE_EXCEEDED"
    assert result.effective_slippage_pct > Decimal("0.01")


def test_reconcile_success_when_all_good() -> None:
    tx = {"hash": "0xabc", "status": 1, "receipt": {"status": 1, "gasUsed": 100000, "blockNumber": 123}}
    balance_before = {"CAKE": Decimal("0")}
    reconciler = ExecutionReconciler(FakeBalances({"CAKE": Decimal("99.5")}))
    result = reconciler.reconcile(tx, Decimal("100"), Decimal("0.01"), balance_before)
    assert result.status == "SUCCESS"
    assert result.balance_delta_confirmed is True


def test_reconcile_uses_tx_balance_after_edge() -> None:
    tx = {"hash": "0xabc", "status": 1, "receipt": {"status": 1}, "balance_after": {"CAKE": "100"}}
    result = ExecutionReconciler(FakeBalances({})).reconcile(
        tx, Decimal("100"), Decimal("0.01"), {"CAKE": Decimal("0")}
    )
    assert result.status == "SUCCESS"


def test_reconcile_fails_when_balance_delta_zero_edge() -> None:
    tx = {"hash": "0xabc", "status": 1, "receipt": {"status": 1}}
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("0")})).reconcile(
        tx, Decimal("100"), Decimal("0.01"), {"CAKE": Decimal("0")}
    )
    assert result.status == "FAILED"


def test_reconcile_twak_success_without_receipt_uses_output_amount() -> None:
    tx = {
        "mode": "twak",
        "tool": "swap",
        "returncode": 0,
        "hash": "0x8eb16d186e2d043bd455468590099f21ce85ec7bc357b344085dbbd7fbaecb8e",
        "output": "0.000692298294036133 LTC",
        "minReceived": "0.000688836802565952 LTC",
        "token_out": "LTC",
    }
    result = ExecutionReconciler(FakeBalances({})).reconcile(
        tx,
        Decimal("0.0006992727243723414"),
        Decimal("0.005"),
        {"LTC": Decimal("0")},
    )
    assert result.status == "SUCCESS"
    assert result.tx_hash == tx["hash"]
    assert result.amount_out_actual == Decimal("0.000692298294036133")
    assert result.token_out == "LTC"


def test_reconcile_uses_log_amount_when_higher() -> None:
    tx = {"hash": "0xabc", "status": 1, "amount_out": "101", "receipt": {"status": 1}, "balance_after": {"CAKE": "100"}}
    result = ExecutionReconciler(FakeBalances({})).reconcile(
        tx, Decimal("100"), Decimal("0.01"), {"CAKE": Decimal("0")}
    )
    assert result.amount_out_actual == Decimal("101")



def test_reconcile_exit_twak_success_without_balance() -> None:
    tx = {
        "mode": "twak",
        "tool": "swap",
        "returncode": 0,
        "hash": "0xabc",
        "token_in": "CAKE",
        "amount_sold": "100",
    }
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("100")})).reconcile_exit(
        tx,
        {"CAKE": Decimal("100")},
        {"CAKE": Decimal("100")},
        amount_sold="100",
        token_in="CAKE",
    )
    assert result.status == "SUCCESS"
    assert result.balance_delta_confirmed is False
    assert result.tx_hash == "0xabc"


def test_reconcile_exit_receipt_status_1_overrides_stale_balance() -> None:
    tx = {
        "hash": "0xabc",
        "token_in": "CAKE",
        "receipt": {"status": 1, "gasUsed": 100000, "blockNumber": 123},
    }
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("100")})).reconcile_exit(
        tx,
        {"CAKE": Decimal("100")},
        {"CAKE": Decimal("100")},
        amount_sold="100",
        token_in="CAKE",
    )
    assert result.status == "SUCCESS"
    assert result.balance_delta_confirmed is True


def test_reconcile_exit_fails_on_receipt_status_0_even_if_balance_changes() -> None:
    tx = {
        "hash": "0xabc",
        "token_in": "CAKE",
        "receipt": {"status": 0},
    }
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("0")})).reconcile_exit(
        tx,
        {"CAKE": Decimal("100")},
        {"CAKE": Decimal("0")},
        amount_sold="100",
        token_in="CAKE",
    )
    assert result.status == "FAILED"


def test_reconcile_exit_verifies_by_delta_when_receipt_missing() -> None:
    tx = {"hash": "0xabc", "token_in": "CAKE"}
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("2")})).reconcile_exit(
        tx,
        {"CAKE": Decimal("100")},
        {"CAKE": Decimal("2")},
        amount_sold="98",
        token_in="CAKE",
    )
    assert result.status == "SUCCESS"


def test_reconcile_exit_twak_success_uses_expected_amount_for_actual() -> None:
    tx = {
        "mode": "twak",
        "tool": "swap",
        "returncode": 0,
        "hash": "0xabc",
        "token_in": "CAKE",
    }
    result = ExecutionReconciler(FakeBalances({"CAKE": Decimal("50")})).reconcile_exit(
        tx,
        {"CAKE": Decimal("50")},
        {"CAKE": Decimal("50")},
        amount_sold="50",
        token_in="CAKE",
    )
    assert result.status == "SUCCESS"
    assert result.amount_out_expected == Decimal("50")
    assert result.amount_out_actual == Decimal("50")
