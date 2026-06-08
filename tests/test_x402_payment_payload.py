"""Tests for defensive x402 payment requirement parsing and signing."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from src.data.x402_payment import PaymentRequired, X402PaymentSigner

TOKEN = "0x1111111111111111111111111111111111111111"
RECIPIENT = "0x2222222222222222222222222222222222222222"
PRIVATE_KEY = "0x" + "1" * 64


class FakeResponse:
    status_code = 402
    headers = {"content-type": "application/json"}

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def _payment_body() -> dict[str, Any]:
    return {
        "accepts": [
            {
                "asset": TOKEN,
                "payTo": RECIPIENT,
                "maxAmountRequired": "10000",
                "network": "base",
                "chainId": 8453,
                "scheme": "exact",
            }
        ]
    }


def test_extract_payment_requirements_accepts_list(tmp_path: Path) -> None:
    signer = X402PaymentSigner(artifact_path=tmp_path / "x402_402_response.json")

    requirement = signer.extract_payment_requirements(FakeResponse(_payment_body()))

    assert requirement["token"] == TOKEN
    assert requirement["pay_to"] == RECIPIENT
    assert requirement["amount"] == "10000"
    assert requirement["chain_id"] == 8453


def test_payment_missing_fields_raises(tmp_path: Path) -> None:
    signer = X402PaymentSigner(artifact_path=tmp_path / "x402_402_response.json")

    with pytest.raises(PaymentRequired):
        signer.extract_payment_requirements({"accepts": [{"maxAmountRequired": "10000"}]})


def test_build_payment_header_returns_base64_envelope(tmp_path: Path) -> None:
    artifact_path = tmp_path / "x402_402_response.json"
    signer = X402PaymentSigner(ephemeral_key=PRIVATE_KEY, artifact_path=artifact_path)

    header = signer.build_payment_header(FakeResponse(_payment_body()))
    envelope = json.loads(base64.b64decode(header).decode("utf-8"))

    assert envelope["x402Version"] == 1
    assert envelope["scheme"] == "exact"
    assert envelope["network"] == "base"
    assert envelope["payload"]["authorization"]["to"] == RECIPIENT
    assert envelope["payload"]["authorization"]["value"] == "10000"
    assert envelope["payload"]["signature"].startswith("0x")
    assert artifact_path.exists()


def test_build_payment_header_requires_cmc_ephemeral_key(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)
    signer = X402PaymentSigner(artifact_path=tmp_path / "x402_402_response.json")

    with pytest.raises(PaymentRequired, match="CMC_X402_EPHEMERAL_KEY"):
        signer.build_payment_header(FakeResponse(_payment_body()))


def test_extract_payment_requirements_reads_x402_extra_domain(tmp_path: Path) -> None:
    body = _payment_body()
    body["accepts"][0]["extra"] = {"name": "USD Coin", "version": "2"}
    signer = X402PaymentSigner(artifact_path=tmp_path / "x402_402_response.json")

    requirement = signer.extract_payment_requirements(FakeResponse(body))

    assert requirement["asset_name"] == "USD Coin"
    assert requirement["asset_version"] == "2"
