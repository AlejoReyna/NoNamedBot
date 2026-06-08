"""Tests for Trust Wallet Agent Kit CLI command adaptation."""

from __future__ import annotations

from typing import Any

import pytest

from src.execution.twak_interface import TWAKInterface, TWAKResult


def test_swap_uses_current_twak_positional_command(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"amount_out":99.0}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = TWAKInterface().swap("USDC", "CAKE", 100.0, 0.01)

    assert result["amount_out"] == 99.0
    assert captured["command"] == [
        "twak",
        "swap",
        "100.0",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        "--slippage",
        "1",
        "--chain",
        "bsc",
        "--json",
    ]


def test_wallet_address_uses_current_twak_command(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}
    wallet = "0x1111111111111111111111111111111111111111"

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": f'{{"address":"{wallet}"}}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = TWAKInterface().wallet_address("bsc")

    assert result["address"] == wallet
    assert result["tool"] == "wallet-address"
    assert captured["command"] == ["twak", "wallet", "address", "--chain", "bsc", "--json"]


def test_quote_swap_uses_quote_only_command(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"amount_out":0.0008}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = TWAKInterface().quote_swap("USDC", "BNB", 0.5, 0.01)

    assert result["amount_out"] == 0.0008
    assert result["tool"] == "swap-quote"
    assert captured["command"] == [
        "twak",
        "swap",
        "0.5",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "BNB",
        "--slippage",
        "1",
        "--chain",
        "bsc",
        "--quote-only",
        "--json",
    ]


def test_swap_parser_handles_pure_json_stdout() -> None:
    tx_hash = "0x" + "1" * 64
    result = TWAKInterface._swap_payload_from_result(
        TWAKResult(
            command=["twak", "swap"],
            returncode=0,
            stdout=f'{{"amount_out":99.0,"hash":"{tx_hash}"}}',
            stderr="",
        )
    )

    assert result["amount_out"] == 99.0
    assert result["hash"] == tx_hash
    assert result["tx_hash"] == tx_hash
    assert result["mode"] == "twak"
    assert result["tool"] == "swap"
    assert result["command"] == ["twak", "swap"]
    assert result["returncode"] == 0
    assert "raw" not in result


def test_swap_parser_handles_mixed_stdout_with_approval_and_swap_tx() -> None:
    approval_hash = "0x" + "a" * 64
    swap_hash = "0x" + "b" * 64
    approval_url = f"https://bscscan.com/tx/{approval_hash}"
    swap_url = f"https://bscscan.com/tx/{swap_hash}"
    stdout = f"""Swapping 0.5 USDC -> 0.0008 BNB via LiquidMesh
Sending token approval...
Approval tx: {approval_url}
Swap tx: {swap_url}
Swap executed!
{{
  "input": "0.5 USDC",
  "output": "0.0008 BNB",
  "hash": "{swap_hash}",
  "explorer": "{swap_url}"
}}"""

    result = TWAKInterface._swap_payload_from_result(
        TWAKResult(
            command=["twak", "swap"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
    )

    assert result["input"] == "0.5 USDC"
    assert result["hash"] == swap_hash
    assert result["tx_hash"] == swap_hash
    assert result["explorer"] == swap_url
    assert result["approval_hash"] == approval_hash
    assert result["approval_explorer"] == approval_url
    assert result["raw"] == stdout


def test_paper_swap_does_not_broadcast(monkeypatch: Any) -> None:
    def fail_run(command: list[str], **kwargs: object) -> object:
        raise AssertionError("paper swap should not invoke subprocess")

    monkeypatch.setattr("subprocess.run", fail_run)

    result = TWAKInterface(paper_trade=True).swap("USDC", "CAKE", 100.0, 0.01)

    assert result["mode"] == "paper"
    assert result["tool"] == "twak-swap"


def test_request_x402_uses_twak_native_request(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"result":{"ok":true}}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = TWAKInterface().request_x402(
        "https://mcp.coinmarketcap.com/x402/mcp",
        method="POST",
        body={"jsonrpc": "2.0"},
        max_payment_atomic="10000",
        prefer_network="base",
        prefer_method="eip3009",
        prefer_asset="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )

    assert result["result"] == {"ok": True}
    assert result["tool"] == "x402-request"
    assert captured["command"] == [
        "twak",
        "x402",
        "request",
        "https://mcp.coinmarketcap.com/x402/mcp",
        "--method",
        "POST",
        "--max-payment",
        "10000",
        "--prefer-network",
        "base",
        "--prefer-method",
        "eip3009",
        "--prefer-asset",
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "--yes",
        "--json",
        "--body",
        '{"jsonrpc":"2.0"}',
    ]


def test_swap_converts_fraction_slippage_to_cli_percent(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"amount_out":99.0}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    TWAKInterface().swap("USDC", "CAKE", 100.0, 0.0025)

    assert captured["command"] == [
        "twak",
        "swap",
        "100.0",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        "--slippage",
        "0.25",
        "--chain",
        "bsc",
        "--json",
    ]


VERIFIED_QUOTE_JSON = """{
  "input": "0.5 USDC",
  "output": "0.000818831848165439 BNB",
  "minReceived": "0.000810643529683785 BNB",
  "provider": "LiquidMesh",
  "priceImpact": "0"
}"""


def test_twak_quote_parsing(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": VERIFIED_QUOTE_JSON,
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    slippage = TWAKInterface().estimate_slippage_pct(0.5, "USDC", "BNB")

    assert slippage is not None
    assert slippage == pytest.approx(0.0)
    assert captured["command"] == [
        "twak",
        "swap",
        "0.5",
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "BNB",
        "--chain",
        "bsc",
        "--quote-only",
        "--json",
    ]


def test_twak_quote_uses_min_received_when_price_impact_missing(monkeypatch: Any) -> None:
    quote_json = """{
  "output": "0.000818831848165439 BNB",
  "minReceived": "0.000810643529683785 BNB",
  "provider": "LiquidMesh"
}"""

    def fake_run(command: list[str], **kwargs: object) -> object:
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": quote_json, "stderr": ""},
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    slippage = TWAKInterface().estimate_slippage_pct(0.5, "USDC", "BNB")

    assert slippage is not None
    assert slippage == pytest.approx(0.01, rel=1e-3)


def test_twak_quote_returns_none_on_token_not_found(monkeypatch: Any) -> None:
    def fake_run(command: list[str], **kwargs: object) -> object:
        return type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": '{"error":"Unknown token","errorCode":"TOKEN_NOT_FOUND"}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    assert TWAKInterface().estimate_slippage_pct(0.5, "USDC", "FAKE") is None


def test_twak_failure_reports_stdout_when_stderr_is_empty(monkeypatch: Any) -> None:
    def fake_run(command: list[str], **kwargs: object) -> object:
        return type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": '{"error":"PASSWORD_MISSING"}',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="PASSWORD_MISSING"):
        TWAKInterface().request_x402(
            "https://mcp.coinmarketcap.com/x402/mcp",
            method="POST",
            body={"jsonrpc": "2.0"},
            max_payment_atomic="10000",
            prefer_asset="USDC",
        )
