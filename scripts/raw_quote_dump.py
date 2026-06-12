"""Dump the raw keyless CMC quotes payload for given symbols (free, read-only).

    cd ~/cascade-ai && PYTHONPATH=. .venv/bin/python scripts/raw_quote_dump.py DOGE LINK
"""

from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

load_dotenv(".env")

from src.config.settings import load_settings  # noqa: E402
from src.data.cmc_mcp_client import CMCMCPClient  # noqa: E402


def main() -> int:
    symbols = [s.upper() for s in sys.argv[1:]] or ["DOGE"]
    client = CMCMCPClient(load_settings())
    payload = client._fetch_keyless(  # noqa: SLF001
        "get_crypto_quotes_latest",
        {"symbol": ",".join(symbols)},
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    print("payload['data'] type:", type(data).__name__)
    if isinstance(data, dict):
        for key, value in data.items():
            print(f"\n=== data[{key!r}] is {type(value).__name__}", end="")
            if isinstance(value, list):
                print(f" with {len(value)} entries ===")
                for i, entry in enumerate(value[:4]):
                    if isinstance(entry, dict):
                        q = (entry.get("quote") or {}).get("USD") or {}
                        print(
                            f"  [{i}] id={entry.get('id')} name={entry.get('name')!r} "
                            f"rank={entry.get('cmc_rank')} active={entry.get('is_active')} "
                            f"price={q.get('price')}"
                        )
            else:
                print(" ===")
                print(json.dumps(value, default=str)[:500])
    else:
        print(json.dumps(payload, default=str)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
