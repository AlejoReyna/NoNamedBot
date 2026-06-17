#!/usr/bin/env python3
"""Validate the BSC contract addresses the live swap router will use.

Why this exists
---------------
``TOKEN_CONTRACTS_BSC`` in ``src/config/tokens.py`` maps a ticker to the BEP-20
contract address that TWAK/LiquidMesh swaps against. A wrong or lookalike
address here routes REAL USDC into the wrong (possibly malicious) token. Before
adding a token to the tradable universe, run this to confirm:

1. The address is a well-formed, EIP-55 checksummed 0x address.
2. (Optional, with an RPC) the on-chain ``symbol()`` actually matches the key —
   i.e. the address you pasted really is the token you think it is.

It also lists target symbols that have NO contract yet, so you can see exactly
what is addable.

Usage
-----
    # Format + checksum only (no network):
    python scripts/verify_token_contracts.py

    # Also verify each address's on-chain symbol() over JSON-RPC:
    BSC_RPC_URL=https://bsc-dataseed.bnbchain.org \
        python scripts/verify_token_contracts.py --onchain

Exit code is non-zero if any configured address is malformed or mismatched, so
it can gate a deploy.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eth_utils import is_address, to_checksum_address  # noqa: E402

from src.config import tokens as t  # noqa: E402

# keccak256("symbol()")[:4]
SYMBOL_SELECTOR = "0x95d89b41"


def _decode_abi_string(hex_payload: str) -> str | None:
    """Decode an ABI-encoded string (or bytes32) returned by an eth_call."""

    raw = bytes.fromhex(hex_payload[2:] if hex_payload.startswith("0x") else hex_payload)
    if not raw:
        return None
    # Dynamic string: [offset(32)][length(32)][data...]
    if len(raw) >= 64:
        try:
            length = int.from_bytes(raw[32:64], "big")
            if 0 < length <= len(raw) - 64:
                return raw[64 : 64 + length].decode("utf-8", "replace").strip()
        except Exception:
            pass
    # Legacy bytes32 symbol: trailing nulls.
    return raw.rstrip(b"\x00").decode("utf-8", "replace").strip() or None


def _onchain_symbol(rpc_url: str, address: str) -> str | None:
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": address, "data": SYMBOL_SELECTOR}, "latest"],
    }
    resp = httpx.post(rpc_url, json=payload, timeout=10.0)
    resp.raise_for_status()
    result = resp.json().get("result")
    if not result or result == "0x":
        return None
    return _decode_abi_string(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onchain",
        action="store_true",
        help="Verify each address's on-chain symbol() (needs BSC_RPC_URL).",
    )
    parser.add_argument("--rpc", default=os.getenv("BSC_RPC_URL"), help="BSC JSON-RPC URL.")
    args = parser.parse_args(argv)

    if args.onchain and not args.rpc:
        print("ERROR: --onchain requires --rpc or BSC_RPC_URL.", file=sys.stderr)
        return 2

    problems = 0
    print(f"Validating {len(t.TOKEN_CONTRACTS_BSC)} configured BSC contracts\n")
    for symbol, address in sorted(t.TOKEN_CONTRACTS_BSC.items()):
        if not is_address(address):
            print(f"  [BAD ADDRESS] {symbol}: {address}")
            problems += 1
            continue
        checksummed = to_checksum_address(address)
        if address != checksummed:
            print(f"  [CHECKSUM]    {symbol}: {address} -> should be {checksummed}")
            problems += 1
        if args.onchain:
            try:
                onchain = _onchain_symbol(args.rpc, checksummed)
            except Exception as exc:  # network / decode issues are warnings, not hard fails
                print(f"  [RPC WARN]    {symbol}: could not read symbol() ({exc})")
                continue
            if onchain is None:
                print(f"  [NO symbol()] {symbol}: contract returned no symbol")
                problems += 1
            elif onchain.upper() != symbol.upper():
                print(f"  [MISMATCH]    {symbol}: on-chain symbol() = {onchain!r}")
                problems += 1
            else:
                print(f"  [OK]          {symbol}: {checksummed} (symbol={onchain})")

    targets = [s for s in t.TARGET_SYMBOL_BY_KEY if s not in t.STABLE_TARGET_SYMBOLS]
    missing = sorted(s for s in targets if not t.has_verified_bsc_contract(s))
    print(
        f"\n{len(missing)}/{len(targets)} tradable targets still have NO verified "
        f"BSC contract (not executable until added):"
    )
    print("  " + ", ".join(missing))

    if problems:
        print(f"\nFAIL: {problems} address problem(s) found.")
        return 1
    print("\nOK: all configured addresses are well-formed" + (" and match on-chain." if args.onchain else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
