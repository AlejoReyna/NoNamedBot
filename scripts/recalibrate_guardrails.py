#!/usr/bin/env python3
"""Safely re-anchor a STALE PAPER guardrail state to the current paper balance.

Background
----------
``guardrail_state.json`` stores ``portfolio_ath`` as a monotonic high-water
mark. When it is seeded at a notional bankroll the live paper balance never
actually reached (e.g. ``portfolio_ath=10000`` while the paper portfolio is
~$8k), the drawdown is measured against a peak that never happened:

    drawdown = (10000 - 8000) / 10000 = 20%  >=  drawdown_kill_switch_pct (18%)

That latches ``kill_switch=true`` in the state file, and nothing clears it on
its own, so the bot emits ``action=HALT`` / ``risk_state=kill_switch`` every
cycle even though no real capital was lost.

This tool re-anchors the ATH to the current value and clears the latched kill
switch, giving an honest drawdown baseline from "now".

Safety
------
* Refuses to run unless ``PAPER_TRADE`` is true. It will NEVER reset live
  guardrail state, because that would hide a genuine live drawdown.
* Requires ``--confirm`` so it cannot fire by accident.
* Prints a before/after diff and leaves an audit line you can paste into ops
  notes.

Usage
-----
    python scripts/recalibrate_guardrails.py --value 8000 --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import load_settings
from src.strategy.guardrails import Guardrails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--value",
        type=float,
        required=True,
        help="Current paper portfolio value in USDC to re-anchor the ATH to.",
    )
    parser.add_argument(
        "--state-path",
        default=None,
        help="Override guardrail_state.json path (defaults to settings).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required: actually write the recalibrated state.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if not settings.paper_trade:
        print(
            "REFUSED: PAPER_TRADE is false. This tool only recalibrates paper "
            "guardrail state and must not touch live drawdown tracking.",
            file=sys.stderr,
        )
        return 2

    guardrails = Guardrails(settings, state_path=args.state_path)
    print(
        f"Current state: portfolio_ath={guardrails.all_time_high_usdc:.2f} "
        f"kill_switch={guardrails.should_kill_switch()}"
    )

    if not args.confirm:
        print(
            "Dry run (no --confirm). Would re-anchor ATH to "
            f"{args.value:.2f} and clear the kill switch."
        )
        return 0

    result = guardrails.recalibrate_paper_state(args.value)
    print("Recalibrated paper guardrail state:")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
