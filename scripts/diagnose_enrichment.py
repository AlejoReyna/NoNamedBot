#!/usr/bin/env python3
"""Diagnose why RSI / derivatives are missing from the x402 enriched snapshot.

The dual x402 path builds an enriched snapshot every refresh, but RSI and
funding/OI can still come back empty if the paid technicals/derivatives calls
return nothing or use field names the parser does not recognise. This script
makes the SAME paid x402 calls the live loop already makes (read-only, a couple
of symbols, the small already-budgeted x402 cost) and prints exactly which
fields populate, plus the raw technicals payload so you can see the real field
names.

Run on the box that has the funded CMC_X402_EPHEMERAL_KEY (e.g. EC2):

    python scripts/diagnose_enrichment.py            # ETH CAKE BNB ADA
    python scripts/diagnose_enrichment.py ETH TRIA   # specific symbols
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from src.config.settings import load_settings  # noqa: E402
from src.data.cmc_mcp_client import CMCMCPClient  # noqa: E402

SYMBOLS = [s.upper() for s in sys.argv[1:]] or ["ETH", "CAKE", "BNB", "ADA"]


def main() -> int:
    settings = load_settings()
    print(
        f"use_dual_market_data={settings.use_dual_market_data} "
        f"use_keyless_primary={settings.use_keyless_primary} "
        f"x402_fetch_technicals={settings.x402_fetch_technicals} "
        f"x402_enrich_top_n={settings.x402_enrich_top_n}\n"
    )
    client = CMCMCPClient(settings)

    print("== RAW paid x402 technicals payload ==")
    try:
        raw_tech = client._fetch_x402_technicals_id_preferred(SYMBOLS)
        print(json.dumps(raw_tech, indent=2, default=str)[:4000] or "{} (empty)")
    except Exception as exc:  # noqa: BLE001 - diagnostic, surface anything
        print(f"technicals fetch raised: {exc!r}")

    print("\n== RAW keyless derivatives payload ==")
    try:
        raw_deriv = client._fetch_keyless("get_global_crypto_derivatives_metrics", {})
        print(json.dumps(raw_deriv, indent=2, default=str)[:2000] or "{} (empty)")
    except Exception as exc:  # noqa: BLE001
        print(f"derivatives fetch raised: {exc!r}")

    print("\n== enriched snapshot field presence (what the engine actually sees) ==")
    snap = client.fetch_x402_enriched_snapshot(SYMBOLS, fetch_technicals=True)
    if not snap:
        print("EMPTY enriched snapshot — x402 quotes unavailable (budget/funds/signer?).")
        return 1
    for sym in SYMBOLS:
        row = snap.get(sym) or snap.get(sym.upper()) or {}
        if not row:
            print(f"{sym}: NOT IN SNAPSHOT (no CMC id / skipped on paid layer)")
            continue
        print(
            f"{sym}: rsi={row.get('rsi')!r}  funding_rate={row.get('funding_rate')!r}  "
            f"oi_change_pct={row.get('open_interest_change_pct')!r}  price={row.get('price')!r}"
        )
        print(f"     all keys: {sorted(row.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
