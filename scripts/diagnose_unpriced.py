"""Diagnose which allowlist tokens come back unpriced from the keyless CMC path.

Read-only and FREE: uses only fetch_keyless_quotes_snapshot (no x402 payment,
no governor spend). Run on the box with the production .env:

    cd ~/cascade-ai && .venv/bin/python scripts/diagnose_unpriced.py
"""

from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

load_dotenv(".env")

from src.config.settings import load_settings  # noqa: E402
from src.config.tokens import (  # noqa: E402
    CMC_IDS_BY_SYMBOL,
    TARGET_SYMBOLS,
)
from src.data.cmc_mcp_client import CMCMCPClient  # noqa: E402


def main() -> int:
    settings = load_settings()
    client = CMCMCPClient(settings)
    allowlist = [s.upper() for s in TARGET_SYMBOLS]

    snapshot = client.fetch_keyless_quotes_snapshot(list(TARGET_SYMBOLS))
    priced: set[str] = set()
    null_price: set[str] = set()
    for symbol, row in snapshot.items():
        key = symbol.upper()
        price = row.get("price") if isinstance(row, dict) else None
        try:
            ok = price is not None and float(price) > 0
        except (TypeError, ValueError):
            ok = False
        (priced if ok else null_price).add(key)

    absent = sorted(set(allowlist) - priced - null_price)
    null_price_l = sorted(null_price)

    print(f"allowlist: {len(allowlist)}")
    print(f"priced:    {len(priced)}")
    print(f"returned but null/zero price ({len(null_price_l)}): {null_price_l}")
    print(f"absent from response entirely ({len(absent)}): {absent}")

    # For the broken ones, show whether we have a CMC id (id-based lookup
    # bypasses ticker collisions) and probe each one individually so the raw
    # CMC answer is visible.
    broken = null_price_l + absent
    print("\nper-symbol detail (keyless, free):")
    for symbol in broken:
        has_id = CMC_IDS_BY_SYMBOL.get(symbol)
        try:
            payload = client.fetch_keyless_quotes_snapshot([symbol])
            row = payload.get(symbol) or next(iter(payload.values()), None) if payload else None
            summary = (
                {k: row.get(k) for k in ("symbol", "price", "volume_24h", "market_cap")}
                if isinstance(row, dict)
                else None
            )
        except Exception as exc:  # noqa: BLE001
            summary = f"ERROR: {exc}"
        print(f"  {symbol:12} cmc_id={has_id or '-':>10}  solo_fetch={json.dumps(summary, default=str)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
