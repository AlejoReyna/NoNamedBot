"""Tests for the official x402 SDK CMC client."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from x402 import x402ClientSync

from src.data.x402_client import DEFAULT_PAYMENT_ASSET, X402Client

ENDPOINT = "https://mcp.coinmarketcap.com/x402/mcp"
TEST_PRIVATE_KEY = "0x" + "11" * 32


class FakeSdkClient(x402ClientSync):
    """Minimal SDK client stub for HTTP adapter injection tests."""


def test_request_with_x402_posts_with_cmc_headers() -> None:
    client = X402Client(
        endpoint=ENDPOINT,
        payment_private_key=TEST_PRIVATE_KEY,
        sdk_client=FakeSdkClient(),
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"jsonrpc":"2.0","result":{"ok":true}}'

    mock_session = MagicMock()
    mock_session.post.return_value = mock_response
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with patch("src.data.x402_client.x402_requests", return_value=mock_session):
        payload = client.request_with_x402(
            "POST",
            {"jsonrpc": "2.0"},
            {"MCP-Protocol-Version": "2024-11-05", "X-CMC-MCP-API-KEY": "secret"},
        )

    assert payload == {"jsonrpc": "2.0", "result": {"ok": True}}
    mock_session.post.assert_called_once_with(
        ENDPOINT,
        json={"jsonrpc": "2.0"},
        headers={"MCP-Protocol-Version": "2024-11-05", "X-CMC-MCP-API-KEY": "secret"},
        timeout=15.0,
    )


def test_request_with_x402_returns_none_when_payment_fails() -> None:
    from x402.http.clients.requests import PaymentError

    client = X402Client(endpoint=ENDPOINT, payment_private_key=TEST_PRIVATE_KEY, sdk_client=FakeSdkClient())
    mock_session = MagicMock()
    mock_session.post.side_effect = PaymentError("payment rejected")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with patch("src.data.x402_client.x402_requests", return_value=mock_session):
        assert client.request_with_x402("POST", {"a": 1}, {}) is None


def test_request_with_x402_requires_post() -> None:
    client = X402Client(endpoint=ENDPOINT, payment_private_key=TEST_PRIVATE_KEY, sdk_client=FakeSdkClient())
    assert client.request_with_x402("GET", {"a": 1}, {}) is None


def test_x402_max_payment_converts_major_usdc_to_atomic_units() -> None:
    client = X402Client(default_amount="0.01", default_asset=DEFAULT_PAYMENT_ASSET)
    assert client._max_payment_atomic() == "10000"


def test_x402_max_payment_accepts_already_atomic_units() -> None:
    client = X402Client(default_amount="10000", default_asset=DEFAULT_PAYMENT_ASSET)
    assert client._max_payment_atomic() == "10000"


def test_build_sdk_client_registers_base_mainnet_policy() -> None:
    client = X402Client(
        default_amount="0.01",
        default_asset=DEFAULT_PAYMENT_ASSET,
        chain_id=8453,
        payment_private_key=TEST_PRIVATE_KEY,
    )
    sdk = client._build_sdk_client()
    assert isinstance(sdk, x402ClientSync)
    assert len(sdk._policies) >= 2  # prefer_network + max_amount


def test_resolve_payment_private_key_prefers_ephemeral_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMC_X402_EPHEMERAL_KEY", TEST_PRIVATE_KEY)
    monkeypatch.setenv("EVM_PRIVATE_KEY", "0x" + "22" * 32)
    client = X402Client()
    assert client._resolve_payment_private_key() == TEST_PRIVATE_KEY


def test_resolve_payment_private_key_falls_back_to_evm_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)
    monkeypatch.setenv("EVM_PRIVATE_KEY", TEST_PRIVATE_KEY)
    client = X402Client()
    assert client._resolve_payment_private_key() == TEST_PRIVATE_KEY


def test_resolve_payment_private_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)
    client = X402Client()
    with pytest.raises(ValueError, match="x402 payment key missing"):
        client._resolve_payment_private_key()


def test_parse_mcp_response_reads_sse_payload() -> None:
    text = 'event: message\ndata: {"jsonrpc":"2.0","result":{"symbol":"BNB"}}\n\n'
    from src.data.x402_client import _parse_mcp_response

    assert _parse_mcp_response(text) == {"jsonrpc": "2.0", "result": {"symbol": "BNB"}}
