"""Development-only EIP-712 x402 payment helpers.

The submitted track path uses TWAK native `x402 request` so the user's TWAK
wallet signs locally. This module is retained only as a diagnostic fallback for
inspecting raw CMC 402 payloads; it must not be used by the judged trade loop.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

ARTIFACT_402_PATH = Path("artifacts/x402_402_response.json")
BASE_USDC_TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DEFAULT_CHAIN_ID = 8453
DEFAULT_NETWORK = "base"
USDC_DECIMALS = Decimal("1000000")


class PaymentRequired(Exception):
    """Raised when an x402 payment is required but cannot be safely prepared."""


def write_402_response(response: Any, artifact_path: Path = ARTIFACT_402_PATH) -> Path:
    """Persist the raw 402 response details for live CMC payload debugging."""

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    body = _read_json_body(response)
    if body is None:
        body = _response_text(response)
    payload = {
        "status_code": getattr(response, "status_code", None),
        "headers": dict(getattr(response, "headers", {}) or {}),
        "body": body,
    }
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return artifact_path


class X402PaymentSigner:
    """Build an x402 payment header from a CMC-only HTTP 402 response.

    TWAK 0.17.0 has local wallet signing, but the public CLI exposes typed-data
    signing only inside `twak x402 request`, which owns the HTTP retry and
    cannot preserve CMC MCP headers. `twak wallet sign-message` signs a plain
    EIP-191 message, not the EIP-712 TransferWithAuthorization payload required
    by x402 EIP-3009. Until TWAK exposes a sign-only typed-data command, this
    dev-only helper can use a separate, low-balance CMC data micropayment key.
    """

    def __init__(
        self,
        ephemeral_key: str | None = None,
        chain_id: int | None = None,
        max_usdc_per_call: Decimal | str | float | None = None,
        artifact_path: Path = ARTIFACT_402_PATH,
    ) -> None:
        # Ephemeral key for CMC data micropayments only. Trading wallet remains
        # fully self-custodial via TWAK.
        self.ephemeral_key = ephemeral_key or os.getenv("CMC_X402_EPHEMERAL_KEY")
        self.chain_id = chain_id or _env_int("CMC_X402_CHAIN_ID", DEFAULT_CHAIN_ID)
        self.max_usdc_per_call = _decimal_or_default(
            max_usdc_per_call if max_usdc_per_call is not None else os.getenv("CMC_X402_MAX_USDC_PER_CALL"),
            Decimal("0.015"),
        )
        self.artifact_path = artifact_path

    def build_payment_header(self, response_402: Any) -> str:
        """Return a base64-encoded x402 payment envelope for the 402 response."""

        write_402_response(response_402, self.artifact_path)
        requirement = self.extract_payment_requirements(response_402)
        ephemeral_key = self.ephemeral_key
        if not ephemeral_key:
            raise PaymentRequired(
                "CMC_X402_EPHEMERAL_KEY is not configured. TWAK 0.17.0 can sign "
                "plain messages and complete full x402 HTTP requests, but it does "
                "not expose a public sign-only EIP-712 command for CMC MCP paid "
                f"retries; inspect {self.artifact_path} for the payment requirements."
            )

        token = requirement["token"]
        pay_to = requirement["pay_to"]
        if not _is_evm_address(token):
            raise PaymentRequired(f"x402 token is not an EVM address: {token!r}")
        if not _is_evm_address(pay_to):
            raise PaymentRequired(f"x402 recipient is not an EVM address: {pay_to!r}")

        account = Account.from_key(ephemeral_key)
        now = int(time.time())
        authorization = {
            "from": account.address,
            "to": pay_to,
            "value": str(requirement["amount"]),
            "validAfter": str(now - 60),
            "validBefore": str(now + 300),
            "nonce": "0x" + secrets.token_hex(32),
        }
        typed_data = self._build_transfer_with_authorization_typed_data(requirement, authorization, token)

        try:
            signature = self._sign_with_cmc_ephemeral_key(typed_data, ephemeral_key)
        except Exception as exc:  # pragma: no cover - exact errors come from eth-account
            raise PaymentRequired(f"Unable to sign x402 payment envelope: {exc}") from exc

        envelope = {
            "x402Version": 1,
            "scheme": requirement.get("scheme") or "exact",
            "network": requirement.get("network") or DEFAULT_NETWORK,
            "payload": {
                "authorization": authorization,
                "signature": signature,
            },
        }
        encoded = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.b64encode(encoded).decode("ascii")

    def _build_transfer_with_authorization_typed_data(
        self,
        requirement: dict[str, Any],
        authorization: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        """Return the EIP-712 payload that CMC x402 verifies for USDC payment."""

        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {
                "name": requirement.get("asset_name") or "USDC",
                "version": requirement.get("asset_version") or "2",
                "chainId": int(requirement.get("chain_id") or self.chain_id),
                "verifyingContract": token,
            },
            "message": authorization,
        }

    @staticmethod
    def _sign_with_cmc_ephemeral_key(typed_data: dict[str, Any], ephemeral_key: str) -> str:
        """Sign a CMC-only x402 typed-data payload with the isolated payment key."""

        # Ephemeral key for CMC data micropayments only. Trading wallet remains
        # fully self-custodial via TWAK. This key must never be imported by
        # src.execution or used for swaps, transfers, approvals, or liquidation.
        signable = encode_typed_data(full_message=typed_data)
        signed = Account.sign_message(signable, ephemeral_key)
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        return signature

    def extract_payment_requirements(self, response_or_body: Any) -> dict[str, Any]:
        """Extract the first usable payment requirement from flexible x402 shapes."""

        body = _read_json_body(response_or_body)
        if body is None:
            body = response_or_body

        for candidate in _payment_requirement_candidates(body):
            normalized = self._normalize_requirement(candidate)
            if normalized:
                return normalized
        raise PaymentRequired(
            "x402 402 response did not include enough payment fields; inspect "
            f"{self.artifact_path} for the live CMC response shape."
        )

    def _normalize_requirement(self, requirement: dict[str, Any]) -> dict[str, Any] | None:
        extra = requirement.get("extra") if isinstance(requirement.get("extra"), dict) else {}
        token = _extract_token(requirement)
        pay_to = _extract_address_field(requirement, ("payTo", "recipient", "to"))
        amount = _string_value(_first_value(requirement, ("maxAmountRequired", "amount", "maxAmount")))
        chain_id = _parse_chain_id(_first_value(requirement, ("chainId", "chain_id")), self.chain_id)
        network = _string_value(_first_value(requirement, ("network", "chain"))) or DEFAULT_NETWORK

        if not token or not pay_to:
            return None
        if not amount:
            if not _is_base_usdc(token):
                return None
            # TODO: Confirm whether CMC ever omits maxAmountRequired. If it does,
            # this intentionally caps the authorization at the configured maximum.
            amount = self._max_usdc_atomic_amount()
        self._raise_if_above_limit(amount)

        return {
            "token": token,
            "pay_to": pay_to,
            "amount": amount,
            "chain_id": chain_id,
            "network": network,
            "scheme": _string_value(_first_value(requirement, ("scheme",))) or "exact",
            "asset_name": _string_value(_first_value(extra, ("name",)))
            or _string_value(_first_value(requirement, ("assetName", "asset_name", "name")))
            or "USDC",
            "asset_version": _string_value(_first_value(extra, ("version",)))
            or _string_value(_first_value(requirement, ("assetVersion", "asset_version")))
            or "2",
            "raw": requirement,
        }

    def _raise_if_above_limit(self, amount: str) -> None:
        amount_major = _amount_to_usdc_major(amount)
        if amount_major is None:
            raise PaymentRequired(f"Unable to compare x402 amount {amount!r} to CMC_X402_MAX_USDC_PER_CALL")
        if amount_major > self.max_usdc_per_call:
            raise PaymentRequired(
                f"x402 amount {amount_major} USDC exceeds CMC_X402_MAX_USDC_PER_CALL="
                f"{self.max_usdc_per_call}"
            )

    def _max_usdc_atomic_amount(self) -> str:
        return str(int(self.max_usdc_per_call * USDC_DECIMALS))


def _payment_requirement_candidates(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    if isinstance(value, list):
        candidates: list[dict[str, Any]] = []
        for item in value:
            candidates.extend(_payment_requirement_candidates(item, depth + 1))
        return candidates
    if not isinstance(value, dict):
        return []

    candidates = []
    if _looks_like_requirement(value):
        candidates.append(value)
    for key in ("accepts", "paymentRequirements", "payment_requirements", "x402", "error", "data", "details"):
        if key in value:
            candidates.extend(_payment_requirement_candidates(value[key], depth + 1))
    return candidates


def _looks_like_requirement(value: dict[str, Any]) -> bool:
    keys = {key.lower() for key in value}
    known = {
        "asset",
        "token",
        "tokenaddress",
        "payto",
        "recipient",
        "to",
        "maxamountrequired",
        "amount",
    }
    return bool(keys & known)


def _extract_token(requirement: dict[str, Any]) -> str | None:
    token = _first_value(requirement, ("tokenAddress", "token", "asset"))
    if isinstance(token, dict):
        token = _first_value(token, ("tokenAddress", "address", "contractAddress", "asset", "token"))
    return _string_value(token)


def _extract_address_field(requirement: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    value = _first_value(requirement, keys)
    if isinstance(value, dict):
        value = _first_value(value, ("address", "account", "to", "recipient", "payTo"))
    return _string_value(value)


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    lowered = {key.lower(): key for key in data}
    for key in keys:
        actual_key = lowered.get(key.lower())
        if actual_key is not None:
            return data[actual_key]
    return None


def _read_json_body(response_or_body: Any) -> Any:
    if isinstance(response_or_body, (dict, list)):
        return response_or_body
    if isinstance(response_or_body, str):
        try:
            return json.loads(response_or_body)
        except json.JSONDecodeError:
            return None
    json_method = getattr(response_or_body, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            return None
    return None


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "")
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return str(text)


def _parse_chain_id(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value), 0)
    except ValueError:
        return default


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _amount_to_usdc_major(amount: str) -> Decimal | None:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if "." in str(amount):
        return value
    return value / USDC_DECIMALS


def _decimal_or_default(value: Decimal | str | float | None, default: Decimal) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value, 0)
    except ValueError:
        return default


def _is_evm_address(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value.strip()))


def _is_base_usdc(token: str) -> bool:
    return token.lower() == BASE_USDC_TOKEN.lower()
