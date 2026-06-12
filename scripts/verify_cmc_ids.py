"""Verify pinned CMC ids resolve to the right asset names (free, read-only).

Fetches id-based quotes for every pinned symbol in one batch and prints
id / name / symbol / price so wrong pins are obvious. Also lists allowlist
symbols still without a pinned id.

    cd ~/cascade-ai && PYTHONPATH=. .venv/bin/python scripts/verify_cmc_ids.py
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv(".env")

from src.config.settings import load_settings  # noqa: E402
from src.config.tokens import CMC_IDS_BY_SYMBOL, TARGET_SYMBOLS  # noqa: E402
from src.data.cmc_mcp_client import CMCMCPClient  # noqa: E402


def main() -> int:
    client = CMCMCPClient(load_settings())
    allowlist = {s.upper() for s in TARGET_SYMBOLS}
    pinned = {sym: cid for sym, cid in CMC_IDS_BY_SYMBOL.items() if sym in allowlist}

    payload = client._fetch_keyless(  # noqa: SLF001
        "get_crypto_quotes_latest",
        {"id": ",".join(sorted(set(pinned.values()), key=int))},
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    rows = data if isinstance(data, dict) else {}

    by_symbol = {str(v.get("symbol", "")).upper(): v for v in rows.values() if isinstance(v, dict)}
    print(f"{'SYMBOL':10} {'PINNED_ID':>9}  {'NAME':32} {'PRICE':>14}  STATUS")
    problems = 0
    for sym, cid in sorted(pinned.items()):
        row = by_symbol.get(sym)
        if row is None:
            print(f"{sym:10} {cid:>9}  {'<not in response>':32} {'-':>14}  CHECK")
            problems += 1
            continue
        name = str(row.get("name", ""))[:32]
        price = row.get("price")
        if price is None:  # production reads nested quote.USD.price too
            quote = row.get("quote")
            usd = quote.get("USD") if isinstance(quote, dict) else None
            price = usd.get("price") if isinstance(usd, dict) else None
        ok = price is not None
        status = "ok" if ok else "NULL PRICE - WRONG ID?"
        if not ok:
            problems += 1
        print(f"{sym:10} {cid:>9}  {name:32} {str(price)[:14]:>14}  {status}")

    unpinned = sorted(allowlist - set(pinned))
    print(f"\nallowlist symbols without pinned id ({len(unpinned)}):")
    print(", ".join(unpinned))
    print(f"\n{problems} problem(s)" if problems else "\nall pinned ids look good")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
