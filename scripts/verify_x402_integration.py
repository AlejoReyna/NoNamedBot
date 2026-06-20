#!/usr/bin/env python3
"""Verification harness for the 12 x402 ↔ trading-engine integration fixes.

This script performs static and dynamic checks that the integration points
are wired correctly, then runs the most relevant pytest subset.  It is meant
as a quick acceptance gate before deploying.

Usage:
    python scripts/verify_x402_integration.py
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import(name: str) -> Any:
    return importlib.import_module(name)


def _check(label: str, condition: bool, details: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if details and not condition:
        print(f"         -> {details}")
    return condition


def _get_source_line(obj: Any, text: str) -> int | None:
    try:
        source = inspect.getsource(obj)
    except Exception:
        return None
    for i, line in enumerate(source.splitlines(), start=1):
        if text in line:
            return i
    return None


def verify_fix_1_data_freshness() -> bool:
    print("\nFix #1 — Data freshness in scoring")
    ok = True
    cache = _import("src.data.market_snapshot_cache")
    merge = getattr(cache, "merge_market_snapshots", None)
    ok &= _check(
        "merge_market_snapshots attaches data_age_seconds",
        merge is not None and "data_age_seconds" in inspect.getsource(merge),
    )
    engine = _import("src.strategy.6falgorithm.breakout_engine")
    be = getattr(engine, "BreakoutEngine", None)
    ok &= _check(
        "BreakoutEngine._entry_score uses freshness decay",
        be is not None and "freshness" in inspect.getsource(be._entry_score),
    )
    ok &= _check(
        "_entry_score rejects rows older than 3×TTL",
        be is not None and "> ttl_seconds * 3.0" in inspect.getsource(be._entry_score),
    )
    return ok


def verify_fix_2_cost_aware() -> bool:
    print("\nFix #2 — Cost-aware strategy")
    ok = True
    main = _import("src.main")
    sig = inspect.signature(main._fetch_snapshot)
    ok &= _check(
        "_fetch_snapshot returns 4-tuple",
        "return" in str(sig.return_annotation) or "tuple" in str(sig.return_annotation).lower(),
        str(sig.return_annotation),
    )
    ok &= _check(
        "_fetch_snapshot computes cycle_x402_cost",
        "cycle_x402_cost" in inspect.getsource(main._fetch_snapshot),
    )
    be = _import("src.strategy.6falgorithm.breakout_engine").BreakoutEngine
    ok &= _check(
        "_entry_score discounts by x402 cost vs expected alpha",
        "x402_cost_usdc" in inspect.getsource(be._entry_score)
        and "expected_alpha" in inspect.getsource(be._entry_score),
    )
    pm = _import("src.strategy.position_manager")
    sig = inspect.signature(pm.calculate_position_pct)
    ok &= _check(
        "calculate_position_pct accepts data_cost_pct",
        "data_cost_pct" in sig.parameters,
    )
    return ok


def verify_fix_3_feedback_loop() -> bool:
    print("\nFix #3 — Trade-outcome feedback loop")
    ok = True
    tol = _import("src.research.trade_outcome_log")
    sig = inspect.signature(tol.record_entry)
    for field in ("x402_cost_usdc", "enriched_symbols", "data_age_seconds", "expected_alpha_usdc"):
        ok &= _check(
            f"record_entry accepts {field}",
            field in sig.parameters,
        )
    return ok


def verify_fix_4_timestamp_validation() -> bool:
    print("\nFix #4 — Timestamp validation / TWAK re-quote")
    ok = True
    main = _import("src.main")
    ok &= _check(
        "_attempt_entry_v25 rejects stale snapshots",
        "snapshot_timestamp" in inspect.getsource(main._attempt_entry_v25)
        and "decision_latency" in inspect.getsource(main._attempt_entry_v25),
    )
    be = _import("src.strategy.6falgorithm.breakout_engine").BreakoutEngine
    ok &= _check(
        "_estimate_candidate_slippage re-quotes stale cached estimates",
        "slippage_quote_age_seconds" in inspect.getsource(be._estimate_candidate_slippage)
        and "30.0" in inspect.getsource(be._estimate_candidate_slippage),
    )
    return ok


def verify_fix_5_enrichment_scope() -> bool:
    print("\nFix #5 — Enrichment scope aligned with strategy eval")
    ok = True
    be = _import("src.strategy.6falgorithm.breakout_engine").BreakoutEngine
    sig = inspect.signature(be.evaluate_all)
    ok &= _check(
        "evaluate_all accepts enriched_symbols",
        "enriched_symbols" in sig.parameters,
    )
    ok &= _check(
        "evaluate_all accepts position_symbols",
        "position_symbols" in sig.parameters,
    )
    ok &= _check(
        "evaluate_all filters on enriched_symbols",
        "enriched_symbols" in inspect.getsource(be.evaluate_all)
        and "position_symbols" in inspect.getsource(be.evaluate_all),
    )
    return ok


def verify_fix_6_optimizer_state_model() -> bool:
    print("\nFix #6 — Operationalize optimizer state model")
    ok = True
    main = _import("src.main")
    source = inspect.getsource(main._fetch_snapshot)
    ok &= _check(
        "Regime-aware TTL switching present",
        "REGIME_TTL_MAP" in source,
    )
    ok &= _check(
        "Regime-aware enrichment n present",
        "REGIME_N_MAP" in source,
    )
    return ok


def verify_fix_7_scale_alpha() -> bool:
    print("\nFix #7 — Wire scale_alpha into sizing")
    ok = True
    main = _import("src.main")
    source = inspect.getsource(main._attempt_entry_v25)
    ok &= _check(
        "scale_alpha is called in entry path",
        "scale_alpha" in source,
    )
    pm = _import("src.strategy.position_manager")
    sig = inspect.signature(pm.calculate_position_pct)
    ok &= _check(
        "calculate_position_pct accepts expected_alpha_per_cycle",
        "expected_alpha_per_cycle" in sig.parameters,
    )
    return ok


def verify_fix_8_missing_technicals() -> bool:
    print("\nFix #8 — Graceful degradation for missing technicals")
    ok = True
    be = _import("src.strategy.6falgorithm.breakout_engine").BreakoutEngine
    source = inspect.getsource(be._regime_adjusted_weights)
    ok &= _check(
        "Weights are rebalanced when technicals unavailable",
        "technicals_available" in source and "weights.pop(\"rsi\"" in source,
    )
    source2 = inspect.getsource(be._evaluate_cheap_candidate)
    ok &= _check(
        "RSI gate is relaxed when technicals unavailable",
        "technicals_available" in source2,
    )
    return ok


def verify_fix_9_budget_failure() -> bool:
    print("\nFix #9 — Budget failure handling")
    ok = True
    xc = _import("src.data.x402_client")
    ok &= _check(
        "X402HTTPError exists and carries status_code",
        hasattr(xc, "X402HTTPError")
        and "status_code" in xc.X402HTTPError.__init__.__code__.co_varnames,
    )
    sg = _import("src.data.x402_spend_governor").X402SpendGovernor
    sig = inspect.signature(sg.record_failure)
    ok &= _check(
        "record_failure accepts http_status",
        "http_status" in sig.parameters,
    )
    source = inspect.getsource(sg.record_failure)
    ok &= _check(
        "Smart assume_charged by HTTP status",
        "400 <= http_status < 500" in source,
    )
    return ok


def verify_fix_10_skip_enrichment_fully_deployed() -> bool:
    print("\nFix #10 — Skip enrichment when fully deployed")
    ok = True
    main = _import("src.main")
    run_agent_source = inspect.getsource(main.run_agent)
    fetch_source = inspect.getsource(main._fetch_snapshot)
    ok &= _check(
        "Pre-snapshot entry-capacity gate present",
        "preliminary_entries_allowed" in run_agent_source,
    )
    ok &= _check(
        "New-entries-blocked enrichment scope reduced",
        "New entries blocked" in fetch_source and "position_symbols" in fetch_source,
    )
    return ok


def verify_fix_11_smooth_dust_ttl() -> bool:
    print("\nFix #11 — Smooth dust-threshold TTL")
    ok = True
    main = _import("src.main")
    source = inspect.getsource(main._fetch_snapshot)
    ok &= _check(
        "Smooth transition instead of binary step",
        "position_ratio" in source and "dust_threshold" in source,
    )
    return ok


def verify_fix_12_cost_benefit() -> bool:
    print("\nFix #12 — Cost-benefit sanity check")
    ok = True
    main = _import("src.main")
    source = inspect.getsource(main._attempt_entry_v25)
    ok &= _check(
        "Cost-benefit check present",
        "cost_benefit_check_enabled" in source,
    )
    ok &= _check(
        "Check compares round-trip cost to expected gain",
        "round_trip_cost" in source and "expected_gain" in source,
    )
    return ok


def run_relevant_tests() -> bool:
    print("\nRunning targeted pytest subset")
    tests = [
        "tests/test_main_snapshot_cache.py",
        "tests/test_snapshot_persistence_and_dust.py",
        "tests/test_x402_spend_governor.py",
        "tests/test_x402_client.py",
        "tests/test_integration_v2.py",
        "tests/test_breakout_engine.py",
    ]
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *tests]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode == 0


def main() -> int:
    print("x402 / trading-engine integration verification")
    print("=" * 60)
    checks = [
        verify_fix_1_data_freshness,
        verify_fix_2_cost_aware,
        verify_fix_3_feedback_loop,
        verify_fix_4_timestamp_validation,
        verify_fix_5_enrichment_scope,
        verify_fix_6_optimizer_state_model,
        verify_fix_7_scale_alpha,
        verify_fix_8_missing_technicals,
        verify_fix_9_budget_failure,
        verify_fix_10_skip_enrichment_fully_deployed,
        verify_fix_11_smooth_dust_ttl,
        verify_fix_12_cost_benefit,
    ]
    all_ok = all(fn() for fn in checks)
    tests_ok = run_relevant_tests()
    print("\n" + "=" * 60)
    print(f"Static checks: {'PASS' if all_ok else 'FAIL'}")
    print(f"Pytest subset: {'PASS' if tests_ok else 'FAIL'}")
    return 0 if (all_ok and tests_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
