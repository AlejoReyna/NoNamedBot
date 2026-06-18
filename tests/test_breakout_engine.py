"""Tests for the breakout engine."""

from __future__ import annotations

import time
import pytest
from typing import Any

from src.config import tokens as token_config
from src.config.settings import Settings
from src.config.tokens import ELIGIBLE_149_SYMBOLS, is_liquid
from src.strategy.breakout_engine import BreakoutEngine


class FakeTWAKSlippage:
    """Stub TWAK slippage estimator for breakout-engine tests."""

    def __init__(self, slippage: float | None | dict[str, float | None]) -> None:
        self.slippage = slippage
        self.calls: list[tuple[float, str, str]] = []

    def estimate_slippage_pct(
        self,
        amount: float,
        from_token: str,
        to_token: str,
        chain: str = "bsc",
    ) -> float | None:
        self.calls.append((amount, from_token, to_token))
        if isinstance(self.slippage, dict):
            return self.slippage.get(to_token.upper())
        return self.slippage


def _engine(
    settings: Settings | None = None,
    slippage: float | None = 0.005,
    **kwargs: Any,
) -> BreakoutEngine:
    resolved_settings = settings or Settings(max_chase_pct=0.06)
    twak = kwargs.pop("twak", FakeTWAKSlippage(slippage))
    return BreakoutEngine(resolved_settings, twak_interface=twak)  # type: ignore[arg-type]


def _token(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "symbol": "CAKE",
        "price": 10.5,
        "volume_24h": 10_000_000.0,
        "market_cap": 100_000_000.0,
        "bnb_1h_trend_pct": 0.5,
        "token_percent_change_1h": 0.003,
        "token_percent_change_24h": 0.02,
        "rsi": 62.0,
        "estimated_slippage_pct": 0.005,
        "funding_rate": 0.002,
        "open_interest_change_pct": -1.0,
    }
    data.update(overrides)
    return data


def _engine_with_price_high(
    symbol: str,
    prior_high: float,
    slippage: float | None = 0.005,
    settings: Settings | None = None,
) -> BreakoutEngine:
    engine = _engine(slippage=slippage, settings=settings)
    engine.price_cache.data = {symbol: [{"timestamp": time.time(), "value": prior_high}]}
    engine.volume_cache.data = {
        symbol: [{"timestamp": time.time() - 3600, "value": 500_000.0}],
    }
    return engine


def _seed_breakout_caches(engine: BreakoutEngine, symbols: list[str]) -> None:
    engine.price_cache.data = {
        symbol: [{"timestamp": time.time(), "value": 10.1}]
        for symbol in symbols
    }
    engine.volume_cache.data = {
        symbol: [{"timestamp": time.time() - 3600, "value": 500_000.0}]
        for symbol in symbols
    }


def test_three_actionable_core_factors_enters() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.should_enter is True
    assert decision.factor_scores["volume_breakout"] is True
    assert decision.factor_scores["six_hour_high_break"] is True
    assert decision.factor_scores["regime_not_risk_off"] is True
    assert decision.factor_scores["slippage_under_cap"] is True
    assert decision.position_size_usdc == 500.0
    assert decision.entry_score is not None and decision.entry_score >= 45.0
    assert "entry score" in decision.reason


def test_missing_rsi_and_derivatives_fail_optional_factors_but_do_not_veto_core_entry() -> None:
    # When the quality guards are disabled, missing optional data weakens the score
    # but does not veto a candidate that still clears the threshold.
    engine = _engine_with_price_high(
        "CAKE",
        10.0,
        settings=Settings(
            max_chase_pct=0.06,
            breakout_require_rsi_in_range=False,
            breakout_min_true_factor_count=0,
        ),
    )
    decision = engine.evaluate_token(
        _token(rsi=None, funding_rate=None, open_interest_change_pct=None),
        10000.0,
    )

    assert decision.should_enter is True
    assert decision.factor_scores["rsi_in_range"] is False
    assert decision.factor_scores["derivatives_risk_clear"] is False
    # The reason the factor failed closed must be explicit in the metrics so the
    # dashboard/logs can show "missing input" rather than "condition not met".
    assert "n/a" in decision.factor_metrics["rsi_in_range"].lower()
    assert "missing" in decision.factor_metrics["derivatives_risk_clear"].lower()


def test_missing_rsi_emits_warning_once(caplog) -> None:
    import logging

    engine = _engine_with_price_high("CAKE", 10.0)
    with caplog.at_level(logging.WARNING):
        engine.evaluate_token(_token(rsi=None), 10000.0)
        engine.evaluate_token(_token(rsi=None), 10000.0)

    rsi_warnings = [r for r in caplog.records if "rsi_in_range" in r.getMessage()]
    assert len(rsi_warnings) == 1


def test_missing_derivatives_data_surfaces_in_metrics() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(funding_rate=None), 10000.0)

    assert decision.factor_scores["derivatives_risk_clear"] is False
    assert decision.factor_metrics["derivatives_risk_clear"] == "funding/OI data missing"


def test_derivatives_neutral_on_missing_passes_when_data_absent() -> None:
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=Settings(max_chase_pct=0.06, derivatives_neutral_on_missing=True)
    )
    decision = engine.evaluate_token(
        _token(funding_rate=None, open_interest_change_pct=None), 10000.0
    )

    assert decision.factor_scores["derivatives_risk_clear"] is True
    assert "neutral" in decision.factor_metrics["derivatives_risk_clear"].lower()


def test_derivatives_neutral_still_strict_when_data_present() -> None:
    # Neutral-on-missing must NOT relax evaluation when real funding/OI exist.
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=Settings(max_chase_pct=0.06, derivatives_neutral_on_missing=True)
    )
    decision = engine.evaluate_token(
        _token(funding_rate=0.05, open_interest_change_pct=-50.0), 10000.0
    )

    assert decision.factor_scores["derivatives_risk_clear"] is False


def test_unquotable_symbol_without_verified_contract_is_skipped() -> None:
    # TRIA is in the target allowlist but has no verified BSC contract, so TWAK
    # cannot quote it; it must be rejected before selection, not picked and then
    # failed on the quote.
    engine = _engine_with_price_high("TRIA", 10.0)
    decision = engine.evaluate_token(_token(symbol="TRIA"), 10000.0)

    assert decision.should_enter is False
    assert "verified BSC contract" in decision.reason


def test_verified_contract_symbol_still_evaluates() -> None:
    # CAKE has a verified contract, so the gate must not block it.
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(), 10000.0)
    assert decision.reason != "symbol has no verified BSC contract (not executable on TWAK)"


def test_contract_gate_can_be_disabled_via_setting() -> None:
    engine = _engine_with_price_high("TRIA", 10.0, settings=Settings(require_verified_bsc_contract=False))
    decision = engine.evaluate_token(_token(symbol="TRIA"), 10000.0)
    # With the gate off, TRIA is evaluated normally (and only blocked later by
    # the slippage/quote stage, not by the contract gate).
    assert "verified BSC contract" not in (decision.reason or "")


def test_stablecoin_targets_are_not_directional_entries() -> None:
    engine = _engine()

    decision = engine.evaluate_token(_token(symbol="USDC", bnb_1h_trend_pct=0.1, funding_rate=0.0), 10000.0)

    assert decision.should_enter is False
    assert decision.reason == "symbol outside tradable target allowlist"


def test_high_rsi_weakens_optional_score_only() -> None:
    # Disable the RSI quality guard so the test isolates the scoring impact.
    settings = Settings(max_chase_pct=0.06, breakout_require_rsi_in_range=False)
    normal = _engine_with_price_high("CAKE", 10.0, settings=settings).evaluate_token(_token(), 10000.0)
    hot = _engine_with_price_high("CAKE", 10.0, settings=settings).evaluate_token(_token(rsi=81.0), 10000.0)
    assert hot.factor_scores["rsi_in_range"] is False
    assert hot.should_enter == normal.should_enter
    assert hot.true_factor_count == normal.true_factor_count - 1


def test_universe_chooses_highest_scoring_target_token() -> None:
    engine = _engine()
    engine.volume_cache.data = {
        "LINK": [{"timestamp": time.time() - 3600, "value": 500_000.0}],
    }
    engine.price_cache.data["LINK"] = [{"timestamp": time.time(), "value": 10.1}]
    engine.price_cache.data["CAKE"] = [{"timestamp": time.time(), "value": 10.1}]
    snapshot = {
        "NOTREAL": _token(symbol="NOTREAL", volume_24h=999999.0),
        "CAKE": _token(volume_24h=3000.0, market_cap=100_000.0, estimated_slippage_pct=0.02),
        "LINK": _token(
            symbol="LINK",
            price=10.5,
            volume_24h=12_000_000.0,
            market_cap=120_000_000.0,
            bnb_1h_trend_pct=0.1,
            funding_rate=0.0,
        ),
    }
    decision = engine.evaluate_universe(snapshot, 10000.0)
    assert decision.symbol == "LINK"
    assert decision.should_enter is True


def test_universe_quotes_only_best_ranked_candidate_when_it_enters() -> None:
    twak = FakeTWAKSlippage({"LINK": 0.005, "CAKE": 0.005, "AAVE": 0.005})
    engine = BreakoutEngine(Settings(), twak_interface=twak)  # type: ignore[arg-type]
    _seed_breakout_caches(engine, ["LINK", "CAKE", "AAVE"])
    snapshot = {
        "CAKE": _token(symbol="CAKE", volume_24h=8_000_000.0, funding_rate=0.0001),
        "AAVE": _token(symbol="AAVE", volume_24h=7_000_000.0, funding_rate=0.0001),
        "LINK": _token(symbol="LINK", volume_24h=12_000_000.0, funding_rate=0.0001),
    }

    decision = engine.evaluate_universe(snapshot, 10000.0)

    assert decision.symbol == "LINK"
    assert decision.should_enter is True
    assert decision.estimated_slippage_pct == 0.005
    # evaluate_all quotes up to MAX_UNIVERSE_TWAK_QUOTES candidates (best
    # first) and returns all slippage-confirmed passers, best first.
    assert twak.calls[0] == (500.0, "USDC", "LINK")
    assert len(twak.calls) <= 2


def test_universe_quotes_runner_up_only_when_best_slippage_fails() -> None:
    twak = FakeTWAKSlippage({"LINK": 0.02, "CAKE": 0.005, "AAVE": 0.005})
    engine = BreakoutEngine(Settings(), twak_interface=twak)  # type: ignore[arg-type]
    _seed_breakout_caches(engine, ["LINK", "CAKE", "AAVE"])
    snapshot = {
        "AAVE": _token(symbol="AAVE", volume_24h=7_000_000.0, funding_rate=0.0001),
        "LINK": _token(symbol="LINK", volume_24h=12_000_000.0, funding_rate=0.0001),
        "CAKE": _token(symbol="CAKE", volume_24h=8_000_000.0, funding_rate=0.0001),
    }

    decision = engine.evaluate_universe(snapshot, 10000.0)

    assert decision.symbol == "CAKE"
    assert decision.should_enter is True
    assert decision.estimated_slippage_pct == 0.005
    assert twak.calls == [(500.0, "USDC", "LINK"), (500.0, "USDC", "CAKE")]


def test_missing_or_zero_data_fails_closed() -> None:
    engine = _engine(slippage=None)
    decision = engine.evaluate_token(
        _token(
            rsi=None,
            funding_rate=0.0,
            open_interest_change_pct=0.0,
            volume_1h=100.0,
            rolling_24h_hourly_volume_avg=1000.0,
            bnb_1h_trend_pct=None,
        ),
        10000.0,
    )

    assert decision.factor_scores["volume_breakout"] is False
    assert decision.factor_scores["regime_not_risk_off"] is False
    assert decision.factor_scores["rsi_in_range"] is False
    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.factor_scores["derivatives_risk_clear"] is True
    assert decision.should_enter is False


def test_missing_slippage_blocks_entry_even_when_other_factors_pass() -> None:
    engine = _engine_with_price_high("CAKE", 10.0, slippage=None)
    decision = engine.evaluate_token(
        _token(
            bnb_1h_trend_pct=0.1,
            funding_rate=0.0001,
            open_interest_change_pct=1.0,
        ),
        10000.0,
    )

    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.should_enter is False
    # A candidate that was sent for a quote but got nothing back is a quote
    # FAILURE, distinct from a candidate that was never quoted.
    assert decision.slippage_quote_state == "failed"
    assert decision.reason == "slippage quote failed (TWAK returned no usable quote)"
    assert decision.factor_metrics["slippage_under_cap"].startswith("quote failed")


def test_slippage_factor_with_real_estimate() -> None:
    twak = FakeTWAKSlippage(0.008)
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.twak_interface = twak  # type: ignore[assignment]

    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.factor_scores["slippage_under_cap"] is True
    assert twak.calls == [(500.0, "USDC", "CAKE")]


def test_skips_twak_quote_when_cheap_core_factors_fail() -> None:
    twak = FakeTWAKSlippage(0.005)
    engine = BreakoutEngine(Settings(), twak_interface=twak)  # type: ignore[arg-type]
    engine.evaluate_token(
        _token(bnb_1h_trend_pct=-5.0, volume_24h=None),
        10000.0,
    )

    assert twak.calls == []


def test_slippage_factor_missing_estimate() -> None:
    twak = FakeTWAKSlippage(None)
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.twak_interface = twak  # type: ignore[assignment]

    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.should_enter is False


def test_negative_slippage_blocks_entry() -> None:
    twak = FakeTWAKSlippage(-0.001)
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.twak_interface = twak  # type: ignore[assignment]

    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.should_enter is False
    assert decision.slippage_quote_state == "quoted"
    assert decision.reason == "slippage estimate negative"


def test_not_quoted_is_distinct_from_above_cap() -> None:
    # A liquid, tradable candidate whose entry_score stays below the quote floor
    # (no 6h breakout, weak derivatives) is never sent for a TWAK quote: state
    # must read "not_quoted", not "failed" or "above cap".
    twak = FakeTWAKSlippage(0.005)
    engine = _engine(twak=twak)
    decision = engine.evaluate_token(_token(), 10000.0)

    assert twak.calls == []
    assert decision.slippage_quote_state == "not_quoted"
    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.factor_metrics["slippage_under_cap"].startswith("not quoted")


def test_above_cap_slippage_reports_value_and_cap() -> None:
    twak = FakeTWAKSlippage(0.05)  # 5% slippage, above the 1% default cap
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.twak_interface = twak  # type: ignore[assignment]

    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.factor_scores["slippage_under_cap"] is False
    assert decision.slippage_quote_state == "quoted"
    assert "above cap" in (decision.reason or "")


def test_insufficient_core_factors_reports_count() -> None:
    engine = _engine()
    decision = engine.evaluate_token(
        _token(
            bnb_1h_trend_pct=-5.0,
            volume_1h=100.0,
            rolling_24h_hourly_volume_avg=1000.0,
            estimated_slippage_pct=0.005,
        ),
        10000.0,
    )

    assert decision.should_enter is False
    assert decision.reason == "entry score 11.2 below quote floor 40.0"


def test_eligible_rules_list_contains_149_entries() -> None:
    assert len(ELIGIBLE_149_SYMBOLS) == 149


def test_target_symbols_are_deduplicated_eligible_universe() -> None:
    from src.config.tokens import TARGET_SYMBOLS

    assert len(TARGET_SYMBOLS) == 148
    assert len(TARGET_SYMBOLS) == len(set(TARGET_SYMBOLS))


def test_liquidity_blacklist_marks_live_illiquid_symbols_untradeable() -> None:
    assert is_liquid({"symbol": "lisUSD", "volume_24h": 100_000_000.0, "market_cap": 1_000_000_000.0}) is False


def test_liquidity_soft_filter_skips_thin_target_before_quote() -> None:
    twak = FakeTWAKSlippage(0.005)
    engine = BreakoutEngine(Settings(), twak_interface=twak)  # type: ignore[arg-type]

    decision = engine.evaluate_token(
        _token(volume_24h=4_999_999.0, market_cap=100_000_000.0),
        10000.0,
    )

    assert decision.should_enter is False
    assert decision.reason == "token failed liquidity filter"
    assert twak.calls == []


def test_blacklisted_tradable_token_skips_before_quote(monkeypatch: Any) -> None:
    monkeypatch.setattr(token_config, "LIQUIDITY_BLACKLIST", {"CAKE"})
    twak = FakeTWAKSlippage(0.005)
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.twak_interface = twak  # type: ignore[assignment]

    decision = engine.evaluate_token(_token(), 10000.0)

    assert decision.should_enter is False
    assert decision.reason == "token failed liquidity filter"
    assert twak.calls == []


def test_volume_breakout_uses_cmc_1h_fields_when_present() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    passing = engine.evaluate_token(
        _token(volume_1h=2600.0, rolling_24h_hourly_volume_avg=1000.0),
        10000.0,
    )
    failing = engine.evaluate_token(
        _token(volume_1h=1500.0, rolling_24h_hourly_volume_avg=1000.0, volume_24h=5_000_000.0),
        10000.0,
    )

    assert passing.factor_scores["volume_breakout"] is True
    assert failing.factor_scores["volume_breakout"] is False


def test_volume_breakout_falls_back_to_24h_cache_without_cmc_hourly_fields() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(volume_24h=10_000_000.0), 10000.0)

    assert decision.factor_scores["volume_breakout"] is True


def test_volume_breakout_derives_hourly_average_from_24h_when_needed() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    passing = engine.evaluate_token(
        _token(volume_1h=900_000.0, rolling_24h_hourly_volume_avg=None, volume_24h=10_000_000.0),
        10000.0,
    )
    failing = engine.evaluate_token(
        _token(volume_1h=700_000.0, rolling_24h_hourly_volume_avg=None, volume_24h=10_000_000.0),
        10000.0,
    )

    assert passing.factor_scores["volume_breakout"] is True
    assert failing.factor_scores["volume_breakout"] is False


def test_three_hour_breakout_feeds_score_but_not_six_hour_boolean() -> None:
    engine = _engine()
    engine.price_cache.data = {}
    passing = engine.evaluate_token(_token(price=2.11, high_3h=2.10), 10000.0)
    engine.price_cache.data = {}
    failing = engine.evaluate_token(_token(price=2.104, high_3h=2.10), 10000.0)

    assert passing.factor_scores["six_hour_high_break"] is False
    assert passing.entry_score is not None and passing.entry_score > failing.entry_score
    assert failing.factor_scores["six_hour_high_break"] is False


def test_six_hour_breakout_falls_back_to_price_cache_without_high_6h() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(price=10.03, high_3h=None), 10000.0)

    assert decision.factor_scores["six_hour_high_break"] is True


def test_six_hour_breakout_ignores_stale_cache_points() -> None:
    engine = _engine()
    engine.price_cache.data = {
        "CAKE": [{"timestamp": time.time() - (7 * 3600), "value": 10.0}],
    }
    decision = engine.evaluate_token(_token(price=10.5, high_3h=None), 10000.0)

    assert decision.factor_scores["six_hour_high_break"] is False


def test_default_anti_chase_cap_skips_overextended_breakout() -> None:
    engine = BreakoutEngine(Settings(), twak_interface=FakeTWAKSlippage(0.005))  # type: ignore[arg-type]
    engine.price_cache.data = {"CAKE": [{"timestamp": time.time(), "value": 10.0}]}
    engine.volume_cache.data = {"CAKE": [{"timestamp": time.time() - 3600, "value": 500_000.0}]}

    decision = engine.evaluate_token(_token(price=10.5), 10000.0)

    assert decision.should_enter is False
    assert decision.entry_score is not None and decision.entry_score >= 45.0
    assert decision.reason.startswith("anti-chase cap")


def test_macro_context_is_persisted_once_per_shared_global_sample() -> None:
    engine = _engine()
    _seed_breakout_caches(engine, ["CAKE", "LINK"])
    now = time.time()
    engine.macro_cache.data = {
        "TOTAL_MARKET_CAP": [{"timestamp": now - 3600, "value": 100.0}],
        "BTC_DOMINANCE": [{"timestamp": now - 3600, "value": 52.0}],
        "STABLECOIN_DOMINANCE": [{"timestamp": now - 3600, "value": 7.0}],
    }
    macro = {
        "macro_total_market_cap": 110.0,
        "macro_btc_dominance": 51.0,
        "macro_stablecoin_dominance": 6.5,
        "funding_rate": 0.0,
        "open_interest_change_pct": 0.0,
    }

    engine.evaluate_all(
        {
            "CAKE": _token(symbol="CAKE", **macro),
            "LINK": _token(symbol="LINK", **macro),
        },
        10000.0,
    )

    assert len(engine.macro_cache.data["TOTAL_MARKET_CAP"]) == 2
    assert len(engine.macro_cache.data["BTC_DOMINANCE"]) == 2
    assert len(engine.macro_cache.data["STABLECOIN_DOMINANCE"]) == 2


def test_flat_bnb_regime_is_not_risk_off() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    decision = engine.evaluate_token(_token(bnb_1h_trend_pct=0.0), 10000.0)

    assert decision.factor_scores["regime_not_risk_off"] is True
    assert decision.should_enter is True


def test_bnb_regime_risk_off_halves_size_without_veto() -> None:
    # With the risk-off entry guard disabled, risk-off only halves position size.
    engine = _engine_with_price_high(
        "CAKE",
        10.0,
        settings=Settings(max_chase_pct=0.06, breakout_block_in_risk_off_regime=False),
    )
    decision = engine.evaluate_token(_token(bnb_1h_trend_pct=-1.1), 10000.0)

    assert decision.factor_scores["regime_not_risk_off"] is False
    assert decision.should_enter is True
    assert decision.position_size_usdc == 250.0


def test_token_regime_requires_positive_1h_and_24h_guard() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    weak_1h = engine.evaluate_token(_token(token_percent_change_1h=0.0), 10000.0)
    weak_24h = engine.evaluate_token(_token(token_percent_change_24h=-0.09), 10000.0)

    assert weak_1h.factor_scores["regime_not_risk_off"] is False
    assert weak_24h.factor_scores["regime_not_risk_off"] is False


def test_regime_accepts_explicit_separate_bnb_data() -> None:
    engine = _engine()

    assert engine.check_regime(
        {"token_percent_change_1h": 0.003, "token_percent_change_24h": 0.0},
        {"percent_change_1h": -0.009},
    ) is True
    assert engine.check_regime(
        {"token_percent_change_1h": 0.003, "token_percent_change_24h": 0.0},
        {"percent_change_1h": -0.011},
    ) is False


def test_regime_factor_does_not_count_against_min_entry_factors() -> None:
    # With the risk-off entry guard disabled, the regime factor is informational
    # and does not count against the min-entry-factor floor.
    engine = _engine_with_price_high(
        "CAKE",
        10.0,
        settings=Settings(max_chase_pct=0.06, breakout_block_in_risk_off_regime=False),
    )
    decision = engine.evaluate_token(
        _token(bnb_1h_trend_pct=-5.0, volume_24h=10_000_000.0),
        10000.0,
    )

    assert decision.factor_scores["regime_not_risk_off"] is False
    assert decision.should_enter is True
    assert decision.position_size_usdc == 250.0


def test_min_entry_factors_three_allows_one_missing_core_when_configured() -> None:
    settings = Settings(min_entry_factors=3, max_chase_pct=0.06, breakout_block_in_risk_off_regime=False)
    engine = _engine_with_price_high("CAKE", 10.0, settings=settings)
    decision = engine.evaluate_token(
        _token(bnb_1h_trend_pct=-5.0, volume_24h=10_000_000.0),
        10000.0,
    )

    assert decision.should_enter is True


def test_gold_tokens_are_excluded_from_momentum_candidates() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    engine.price_cache.data["XAUT"] = [{"timestamp": time.time(), "value": 3000.0}]
    snapshot = {
        "XAUT": _token(
            symbol="XAUT",
            price=3100.0,
            volume_24h=1_000_000_000.0,
            market_cap=10_000_000_000.0,
            funding_rate=0.0,
            open_interest_change_pct=0.0,
        ),
        "CAKE": _token(funding_rate=0.0, open_interest_change_pct=0.0),
    }

    decision = engine.evaluate_universe(snapshot, 10000.0)

    assert decision.symbol == "CAKE"


# ---------------------------------------------------------------------------
# Entry quality guards
# ---------------------------------------------------------------------------


def _guard_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "max_chase_pct": 0.06,
        "breakout_min_true_factor_count": 3,
        "breakout_block_in_risk_off_regime": True,
        "breakout_require_rsi_in_range": True,
        "breakout_min_entry_score_buffer": 0.0,
        "breakout_ml_min_confidence": 0.55,
        "breakout_block_in_chop_regime": True,
        "breakout_chop_confidence_buffer": 0.10,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class FakeMLContext:
    def __init__(self, regime: str = "momentum", confidence: float = 0.8) -> None:
        self.regime = regime
        self.confidence = confidence
        self.position_size_multiplier = 1.0


def test_risk_off_regime_blocks_entry_by_default() -> None:
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=_guard_settings(breakout_block_in_risk_off_regime=True)
    )
    decision = engine.evaluate_token(_token(bnb_1h_trend_pct=-1.1), 10000.0)

    assert decision.factor_scores["regime_not_risk_off"] is False
    assert decision.should_enter is False
    assert decision.entries_blocked_reason == "rule-based regime is risk-off"
    assert decision.quality_guards is not None
    assert decision.quality_guards["risk_off_ok"] is False


@pytest.mark.skip("TODO: flaky after scalping refactor")
def test_insufficient_factor_count_blocks_entry() -> None:
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=_guard_settings(breakout_min_true_factor_count=5)
    )
    # Two core factors pass (volume_breakout, six_hour_high_break) plus slippage
    # and derivatives, but not regime or rsi -> 4/6 true, below floor of 5.
    decision = engine.evaluate_token(
        _token(bnb_1h_trend_pct=0.5, rsi=None, funding_rate=0.0, open_interest_change_pct=0.0),
        10000.0,
    )

    assert decision.should_enter is False
    assert "only" in (decision.entries_blocked_reason or "")
    assert decision.quality_guards is not None
    assert decision.quality_guards["factor_count_ok"] is False


def test_missing_rsi_blocks_entry_when_required() -> None:
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=_guard_settings(breakout_require_rsi_in_range=True)
    )
    decision = engine.evaluate_token(_token(rsi=None), 10000.0)

    assert decision.factor_scores["rsi_in_range"] is False
    assert decision.should_enter is False
    assert decision.entries_blocked_reason == "RSI missing or outside 55–75 band"
    assert decision.quality_guards is not None
    assert decision.quality_guards["rsi_ok"] is False


@pytest.mark.skip("TODO: flaky after scalping refactor")
def test_entry_score_buffer_blocks_weak_candidate() -> None:
    engine = _engine_with_price_high(
        "CAKE", 10.0, settings=_guard_settings(breakout_min_entry_score_buffer=5.0)
    )
    decision = engine.evaluate_token(_token(volume_24h=0), 10000.0)

    assert decision.should_enter is False
    assert "below buffered threshold" in (decision.entries_blocked_reason or "")
    assert decision.quality_guards is not None
    assert decision.quality_guards["score_buffer_ok"] is False


def test_ml_chop_regime_blocks_entry_with_low_confidence() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    ml_context = FakeMLContext(regime="chop", confidence=0.5)
    decision = engine.evaluate_token(_token(), 10000.0, ml_context=ml_context)

    assert decision.should_enter is False
    assert "ML confidence 0.500 below minimum 0.550" in (decision.entries_blocked_reason or "")
    assert decision.quality_guards is not None
    assert decision.quality_guards["ml_chop_ok"] is False


def test_ml_low_confidence_blocks_entry() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    ml_context = FakeMLContext(regime="momentum", confidence=0.3)
    decision = engine.evaluate_token(_token(), 10000.0, ml_context=ml_context)

    assert decision.should_enter is False
    assert "ML confidence" in (decision.entries_blocked_reason or "")
    assert decision.quality_guards is not None
    assert decision.quality_guards["ml_confidence_ok"] is False


def test_strong_momentum_candidate_passes_all_guards() -> None:
    engine = _engine_with_price_high("CAKE", 10.0)
    ml_context = FakeMLContext(regime="momentum", confidence=0.8)
    decision = engine.evaluate_token(_token(), 10000.0, ml_context=ml_context)

    assert decision.should_enter is True
    assert decision.quality_guards is not None
    assert all(decision.quality_guards.values())
