"""Tests for live preflight CLI readiness checks."""

from __future__ import annotations

from typing import Any

import pytest

from src import main as main_module
from src.config.settings import Settings


WALLET = "0x1111111111111111111111111111111111111111"


def test_preflight_requires_live_mode() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["--preflight"])


def test_live_preflight_passes_with_mocked_read_only_checks(monkeypatch: Any, capsys: Any) -> None:
    settings = Settings(paper_trade=True, wallet_address=WALLET)
    observed: dict[str, object] = {}

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            observed["toolkit_paper_trade"] = live_settings.paper_trade

        def get_balance(self, symbol: str) -> dict[str, object]:
            return {"balance": {"BNB": 0.01, "USDC": 2.5, "USDT": 0.0}[symbol]}

    class FakeTWAK:
        def __init__(self, paper_trade: bool = False) -> None:
            observed["twak_paper_trade"] = paper_trade

        def wallet_address(self, chain: str) -> dict[str, object]:
            observed["wallet_chain"] = chain
            return {"address": WALLET}

        def quote_swap(
            self,
            from_symbol: str,
            to_symbol: str,
            amount: float,
            slippage_pct: float,
        ) -> dict[str, object]:
            observed["quote"] = (from_symbol, to_symbol, amount, slippage_pct)
            return {"amount_out": 0.0008, "command": ["twak", "swap", "--quote-only"]}

        def swap(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("preflight must not broadcast swaps")

    class FakeCMC:
        def __init__(self, live_settings: Settings) -> None:
            observed["cmc_paper_trade"] = live_settings.paper_trade

        def fetch_market_snapshot(self, symbols: list[str]) -> dict[str, dict[str, object]]:
            observed["snapshot_symbols"] = symbols
            return {"CAKE": {"symbol": "CAKE", "price": 2.5}}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)
    monkeypatch.setattr(main_module, "TWAKInterface", FakeTWAK)
    monkeypatch.setattr(main_module, "CMCMCPClient", FakeCMC)

    assert main_module.main(["--live", "--preflight"]) == 0

    output = capsys.readouterr().out
    assert "PASS settings loaded - ok" in output
    assert "PASS settings live mode - PAPER_TRADE=false" in output
    assert "PASS TWAK quote-only - quote-only command parsed" in output
    assert "PASS snapshot target price - 1 priced target(s)" in output
    assert "Preflight result: PASS" in output
    assert observed["toolkit_paper_trade"] is False
    assert observed["twak_paper_trade"] is False
    assert observed["cmc_paper_trade"] is False
    assert observed["wallet_chain"] == "bsc"
    assert observed["quote"] == ("USDC", "BNB", 0.5, 0.01)


def test_live_preflight_reports_settings_load_failure(monkeypatch: Any, capsys: Any) -> None:
    def fail_load_settings() -> Settings:
        raise ValueError("bad CMC_API_KEY=secret")

    monkeypatch.setattr(main_module, "load_settings", fail_load_settings)

    assert main_module.main(["--live", "--preflight"]) == 1

    output = capsys.readouterr().out
    assert "FAIL settings loaded - bad CMC_API_KEY=<redacted>" in output
    assert "Preflight result: FAIL" in output


def test_live_preflight_fails_when_usdc_balance_is_zero(monkeypatch: Any, capsys: Any) -> None:
    settings = Settings(paper_trade=False, wallet_address=WALLET)

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            self.live_settings = live_settings

        def get_balance(self, symbol: str) -> dict[str, object]:
            return {"balance": {"BNB": 0.01, "USDC": 0.0, "USDT": 0.0}[symbol]}

    class FakeTWAK:
        def __init__(self, paper_trade: bool = False) -> None:
            self.paper_trade = paper_trade

        def wallet_address(self, chain: str) -> dict[str, object]:
            return {"address": WALLET}

        def quote_swap(
            self,
            from_symbol: str,
            to_symbol: str,
            amount: float,
            slippage_pct: float,
        ) -> dict[str, object]:
            return {"amount_out": 0.0008, "command": ["twak", "swap", "--quote-only"]}

        def swap(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("preflight must not broadcast swaps")

    class FakeCMC:
        def __init__(self, live_settings: Settings) -> None:
            self.live_settings = live_settings

        def fetch_market_snapshot(self, symbols: list[str]) -> dict[str, dict[str, object]]:
            return {"CAKE": {"symbol": "CAKE", "price": 2.5}}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)
    monkeypatch.setattr(main_module, "TWAKInterface", FakeTWAK)
    monkeypatch.setattr(main_module, "CMCMCPClient", FakeCMC)

    assert main_module.main(["--live", "--preflight"]) == 1

    output = capsys.readouterr().out
    assert "FAIL USDC balance > 0 - zero" in output
    assert "Preflight result: FAIL" in output


def test_live_preflight_fails_without_priced_target_snapshot(monkeypatch: Any, capsys: Any) -> None:
    settings = Settings(paper_trade=False, wallet_address=WALLET)

    class FakeToolkit:
        def __init__(self, live_settings: Settings) -> None:
            self.live_settings = live_settings

        def get_balance(self, symbol: str) -> dict[str, object]:
            return {"balance": {"BNB": 0.01, "USDC": 2.5, "USDT": 0.0}[symbol]}

    class FakeTWAK:
        def __init__(self, paper_trade: bool = False) -> None:
            self.paper_trade = paper_trade

        def wallet_address(self, chain: str) -> dict[str, object]:
            return {"address": WALLET}

        def quote_swap(
            self,
            from_symbol: str,
            to_symbol: str,
            amount: float,
            slippage_pct: float,
        ) -> dict[str, object]:
            return {"amount_out": 0.0008, "command": ["twak", "swap", "--quote-only"]}

    class FakeCMC:
        def __init__(self, live_settings: Settings) -> None:
            self.live_settings = live_settings

        def fetch_market_snapshot(self, symbols: list[str]) -> dict[str, dict[str, object]]:
            return {"CAKE": {"symbol": "CAKE"}}

    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "BnbToolkitWrapper", FakeToolkit)
    monkeypatch.setattr(main_module, "TWAKInterface", FakeTWAK)
    monkeypatch.setattr(main_module, "CMCMCPClient", FakeCMC)

    assert main_module.main(["--live", "--preflight"]) == 1

    output = capsys.readouterr().out
    assert "PASS CMC/x402 market snapshot - 1 item(s)" in output
    assert "FAIL snapshot target price - none" in output
