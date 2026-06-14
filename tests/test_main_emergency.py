"""Tests for emergency liquidation startup behavior."""

from __future__ import annotations

import json

import pytest

from src.config.settings import Settings
from src import main as main_module
from src.strategy.position_manager import PositionManager


def test_emergency_liquidate_defaults_to_live_mode(monkeypatch: object, tmp_path: object) -> None:
    state_path = tmp_path / "positions.json"  # type: ignore[operator]
    guardrail_path = tmp_path / "guardrail_state.json"  # type: ignore[operator]
    settings = Settings(
        paper_trade=True,
        position_state_path=str(state_path),
        guardrail_state_path=str(guardrail_path),
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    seeded = PositionManager(settings)
    seeded.open_position("CAKE", amount_tokens=2.0, entry_price=3.0, entry_value_usdc=6.0)
    observed: dict[str, object] = {}

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            observed["paper_trade"] = live_settings.paper_trade

    class FakeTWAK:
        def __init__(self, paper_trade: bool = False) -> None:
            observed["twak_paper_trade"] = paper_trade

        def swap(self, from_symbol: str, to_symbol: str, amount: float, slippage_pct: float) -> dict[str, object]:
            observed["swap"] = (from_symbol, to_symbol, amount, slippage_pct)
            return {"amount_out": amount, "tx_hash": "0x" + "1" * 64}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "TWAKInterface", FakeTWAK)  # type: ignore[attr-defined]

    assert main_module.main(["--emergency-liquidate"]) == 0
    assert observed["paper_trade"] is False
    assert observed["twak_paper_trade"] is False
    assert observed["swap"] == ("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 2.0, 0.01)


def test_emergency_liquidate_continues_after_failed_position(monkeypatch: object, tmp_path: object) -> None:
    settings = Settings(
        paper_trade=False,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    position_manager.open_position("ATOM", amount_tokens=0.0528, entry_price=2.0, entry_value_usdc=0.1056)
    position_manager.open_position("CAKE", amount_tokens=2.0, entry_price=3.0, entry_value_usdc=6.0)
    guardrails = main_module.Guardrails(settings)
    calls: list[str] = []

    def fake_execute_logged_swap(
        settings_arg: Settings,
        router: object,
        action: str,
        from_symbol: str,
        to_symbol: str,
        amount_in: float,
        max_slippage_pct: float,
        **kwargs: object,
    ) -> dict[str, object]:
        del settings_arg, router, action, to_symbol, amount_in, max_slippage_pct, kwargs
        calls.append(from_symbol)
        if from_symbol == "ATOM":
            raise RuntimeError("twak swap failed with exit code 1: insufficient balance")
        return {"tx_hash": "0x" + "1" * 64}

    monkeypatch.setattr(main_module, "_execute_logged_swap", fake_execute_logged_swap)

    main_module.emergency_liquidate(position_manager, object(), guardrails)  # type: ignore[arg-type]

    assert calls == ["ATOM", "CAKE"]
    assert position_manager.get_position("ATOM") is not None
    assert position_manager.get_position("CAKE") is None


def test_emergency_liquidate_caps_to_live_wallet_balance(monkeypatch: object, tmp_path: object) -> None:
    settings = Settings(
        paper_trade=False,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    position_manager.open_position("DOGE", amount_tokens=5.59594069, entry_price=0.089, entry_value_usdc=0.498)
    guardrails = main_module.Guardrails(settings)
    calls: list[float] = []

    class FakeToolkit:
        def get_balance(self, symbol: str) -> dict[str, object]:
            assert symbol == "DOGE"
            return {"symbol": "DOGE", "balance": 5.59530611}

    def fake_execute_logged_swap(
        settings_arg: Settings,
        router: object,
        action: str,
        from_symbol: str,
        to_symbol: str,
        amount_in: float,
        max_slippage_pct: float,
        **kwargs: object,
    ) -> dict[str, object]:
        del settings_arg, router, action, from_symbol, to_symbol, max_slippage_pct, kwargs
        calls.append(amount_in)
        return {"tx_hash": "0x" + "1" * 64}

    monkeypatch.setattr(main_module, "_execute_logged_swap", fake_execute_logged_swap)

    main_module.emergency_liquidate(position_manager, object(), guardrails, FakeToolkit())  # type: ignore[arg-type]

    assert calls == [pytest.approx(5.59530611)]
    assert position_manager.get_position("DOGE") is None


def test_emergency_liquidate_removes_zero_balance_stale_position(monkeypatch: object, tmp_path: object) -> None:
    settings = Settings(
        paper_trade=False,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    position_manager.open_position("ATOM", amount_tokens=0.0528, entry_price=2.0, entry_value_usdc=0.1056)
    guardrails = main_module.Guardrails(settings)

    class FakeToolkit:
        def get_balance(self, symbol: str) -> dict[str, object]:
            assert symbol == "ATOM"
            return {"symbol": "ATOM", "balance": 0.0}

    def fail_if_called(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise AssertionError("zero-balance stale positions must not broadcast swaps")

    monkeypatch.setattr(main_module, "_execute_logged_swap", fail_if_called)

    main_module.emergency_liquidate(position_manager, object(), guardrails, FakeToolkit())  # type: ignore[arg-type]

    assert position_manager.get_position("ATOM") is None


def test_balance_command_reads_live_balances(monkeypatch: object, capsys: object) -> None:
    settings = Settings(paper_trade=True)
    observed: dict[str, object] = {}

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            observed["paper_trade"] = live_settings.paper_trade

        def get_balance(self, symbol: str) -> dict[str, object]:
            return {"balance": {"BNB": 0.001, "USDC": 10.5, "USDT": 0.0}[symbol]}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)  # type: ignore[attr-defined]

    assert main_module.main(["--live", "--balance"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert observed["paper_trade"] is False
    assert "BNB: 0.00100000" in output
    assert "USDC: 10.50000000" in output


def test_withdraw_requires_live_mode() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(
            [
                "--withdraw",
                "USDC",
                "--to",
                "0x2222222222222222222222222222222222222222",
                "--amount",
                "1",
            ]
        )


def test_withdraw_command_invokes_transfer(monkeypatch: object, capsys: object) -> None:
    settings = Settings(paper_trade=True)
    observed: dict[str, object] = {}

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            observed["paper_trade"] = live_settings.paper_trade

        def transfer(self, to_address: str, symbol: str, amount: float) -> dict[str, object]:
            observed["transfer"] = (to_address, symbol, amount)
            return {"tx_hash": "0xabc"}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)  # type: ignore[attr-defined]

    assert main_module.main(
        [
            "--live",
            "--withdraw",
            "USDC",
            "--to",
            "0x2222222222222222222222222222222222222222",
            "--amount",
            "1.25",
        ]
    ) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert observed["paper_trade"] is False
    assert observed["transfer"] == ("0x2222222222222222222222222222222222222222", "USDC", 1.25)
    assert "withdraw_tx_hash=0xabc" in output


def test_once_command_limits_live_run_to_one_cycle(monkeypatch: object) -> None:
    settings = Settings(paper_trade=True)
    observed: dict[str, object] = {}

    def fake_run_agent(live_settings: Settings, max_cycles: int | None = None) -> None:
        observed["paper_trade"] = live_settings.paper_trade
        observed["max_cycles"] = max_cycles

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "run_agent", fake_run_agent)  # type: ignore[attr-defined]

    assert main_module.main(["--live", "--once"]) == 0
    assert observed == {"paper_trade": False, "max_cycles": 1}


def test_demo_mode_flag_is_passed_to_run_agent(monkeypatch: object) -> None:
    settings = Settings(paper_trade=True, demo_mode=False)
    observed: dict[str, object] = {}

    def fake_run_agent(live_settings: Settings, max_cycles: int | None = None) -> None:
        observed["paper_trade"] = live_settings.paper_trade
        observed["demo_mode"] = live_settings.demo_mode
        observed["max_cycles"] = max_cycles

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(main_module, "run_agent", fake_run_agent)  # type: ignore[attr-defined]

    assert main_module.main(["--live", "--once", "--demo-mode"]) == 0
    assert observed == {"paper_trade": False, "demo_mode": True, "max_cycles": 1}


def test_demo_cycle_summary_prints_clean_signal(capsys: object) -> None:
    decision = main_module.BreakoutDecision(
        should_enter=True,
        symbol="CAKE",
        position_size_usdc=100.0,
        factor_scores={"slippage_under_cap": True},
        true_factor_count=6,
        reason="4/4 core factors passed (6/6 total)",
        estimated_slippage_pct=0.005,
    )

    main_module._print_demo_cycle_summary(
        1,
        {"CAKE": {"symbol": "CAKE", "price": 2.0}},
        10000.0,
        decision,
        entries_allowed=True,
        position_count=1,
    )

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Cycle 1 summary" in output
    assert "Portfolio: $10,000.00" in output
    assert "Signal: ENTER CAKE factors=6/6 slippage=0.50%" in output
    assert "Reason: 4/4 core factors passed (6/6 total)" in output


class FakeTWAKSlippage:
    def __init__(self, slippage: float | None) -> None:
        self.slippage = slippage

    def estimate_slippage_pct(self, *args: object, **kwargs: object) -> float | None:
        return self.slippage


def test_entry_aborts_before_router_when_slippage_is_missing(tmp_path: object) -> None:
    settings = Settings(
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    guardrails = main_module.Guardrails(settings)
    decision = main_module.BreakoutDecision(
        should_enter=True,
        symbol="CAKE",
        position_size_usdc=100.0,
        factor_scores={"slippage_under_cap": False},
        true_factor_count=5,
        reason="test",
    )

    class FakeRouter:
        calls = 0

        def swap_exact_in(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            return {}

    router = FakeRouter()

    main_module._maybe_enter_position(
        decision,
        position_manager,
        router,  # type: ignore[arg-type]
        guardrails,
        {"CAKE": {"price": 2.0, "estimated_slippage_pct": None}},
        10000.0,
        FakeTWAKSlippage(None),  # type: ignore[arg-type]
    )

    assert router.calls == 0
    assert position_manager.get_position("CAKE") is None


def test_entry_does_not_open_live_position_without_swap_hash(tmp_path: object) -> None:
    settings = Settings(
        paper_trade=False,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    guardrails = main_module.Guardrails(settings)
    decision = main_module.BreakoutDecision(
        should_enter=True,
        symbol="CAKE",
        position_size_usdc=100.0,
        factor_scores={"slippage_under_cap": True},
        true_factor_count=5,
        reason="test",
    )

    class FakeRouter:
        calls: list[tuple[object, ...]] = []

        def swap_exact_in(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((*args, kwargs))
            return {"mode": "twak", "tool": "swap"}

    router = FakeRouter()

    main_module._maybe_enter_position(
        decision,
        position_manager,
        router,  # type: ignore[arg-type]
        guardrails,
        {"CAKE": {"price": 2.0, "estimated_slippage_pct": 0.002}},
        10000.0,
        FakeTWAKSlippage(0.002),  # type: ignore[arg-type]
    )

    assert len(router.calls) == 1
    assert position_manager.get_position("CAKE") is None
    log_record = json.loads((tmp_path / "execution_log.jsonl").read_text(encoding="utf-8").strip())  # type: ignore[operator]
    assert log_record["action"] == "entry"
    assert log_record["result"] == {"mode": "twak", "tool": "swap"}
    assert "tx_hash" not in log_record


def test_entry_opens_paper_position_with_paper_swap_hash(tmp_path: object) -> None:
    settings = Settings(
        paper_trade=True,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    guardrails = main_module.Guardrails(settings)
    decision = main_module.BreakoutDecision(
        should_enter=True,
        symbol="CAKE",
        position_size_usdc=100.0,
        factor_scores={"slippage_under_cap": True},
        true_factor_count=5,
        reason="test",
    )

    class FakeRouter:
        def swap_exact_in(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "mode": "paper",
                "tool": "twak-swap",
                "tx_hash": "paper-twak-swap-USDC-CAKE",
            }

    main_module._maybe_enter_position(
        decision,
        position_manager,
        FakeRouter(),  # type: ignore[arg-type]
        guardrails,
        {"CAKE": {"price": 2.0, "estimated_slippage_pct": 0.002}},
        10000.0,
        FakeTWAKSlippage(0.002),  # type: ignore[arg-type]
    )

    position = position_manager.get_position("CAKE")
    assert position is not None
    assert position.amount_tokens == 50.0
    log_record = json.loads((tmp_path / "execution_log.jsonl").read_text(encoding="utf-8").strip())  # type: ignore[operator]
    assert log_record["tx_hash"] == "paper-twak-swap-USDC-CAKE"


def test_entry_reuses_decision_slippage_without_second_quote(tmp_path: object) -> None:
    settings = Settings(
        paper_trade=True,
        position_state_path=str(tmp_path / "positions.json"),  # type: ignore[operator]
        guardrail_state_path=str(tmp_path / "guardrail_state.json"),  # type: ignore[operator]
        execution_log_path=str(tmp_path / "execution_log.jsonl"),  # type: ignore[operator]
    )
    position_manager = PositionManager(settings)
    guardrails = main_module.Guardrails(settings)
    decision = main_module.BreakoutDecision(
        should_enter=True,
        symbol="CAKE",
        position_size_usdc=100.0,
        factor_scores={"slippage_under_cap": True},
        true_factor_count=6,
        reason="test",
        estimated_slippage_pct=0.002,
    )

    class FakeRouter:
        def swap_exact_in(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "mode": "paper",
                "tool": "twak-swap",
                "tx_hash": "paper-twak-swap-USDC-CAKE",
            }

    class NoQuoteTWAK:
        def estimate_slippage_pct(self, *args: object, **kwargs: object) -> float:
            raise AssertionError("decision slippage should be reused")

    main_module._maybe_enter_position(
        decision,
        position_manager,
        FakeRouter(),  # type: ignore[arg-type]
        guardrails,
        {"CAKE": {"price": 2.0}},
        10000.0,
        NoQuoteTWAK(),  # type: ignore[arg-type]
    )

    assert position_manager.get_position("CAKE") is not None
