"""Six-factor momentum breakout strategy engine adapted to 4-factor core."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.settings import Settings
from src.config.tokens import is_liquid, is_momentum_candidate_symbol, is_tradable_symbol
from src.execution.twak_interface import TWAKInterface

LOGGER = logging.getLogger(__name__)

CORE_FACTOR_COUNT = 3
TOTAL_FACTOR_COUNT = 6
DEFAULT_REFERENCE_WINDOWS_HOURS = (3, 6, 24)


@dataclass(frozen=True)
class BreakoutDecision:
    """Decision returned by the breakout engine."""

    should_enter: bool
    symbol: str | None
    position_size_usdc: float
    factor_scores: dict[str, bool]
    true_factor_count: int
    reason: str
    estimated_slippage_pct: float | None = None
    entry_score: float | None = None
    position_size_multiplier: float = 1.0


@dataclass(frozen=True)
class _CheapCandidate:
    """Candidate factors that can be evaluated without a TWAK quote."""

    symbol: str
    token_data: dict[str, Any]
    position_size_usdc: float
    volume_24h: float
    volume_breakout: bool
    six_hour_high_break: bool
    regime_not_risk_off: bool
    rsi_in_range: bool
    derivatives_risk_clear: bool
    cheap_core_pass_count: int
    true_factor_count_without_slippage: int
    breakout_strength: float
    volume_surge_score: float
    macro_score: float
    macro_size_multiplier: float
    broken_reference_high: float | None
    chase_cap_exceeded: bool
    momentum_1h: float = 0.0
    momentum_24h: float = 0.0


@dataclass(frozen=True)
class _BreakoutProfile:
    six_hour_high_break: bool
    strength: float
    broken_reference_high: float | None
    chase_cap_exceeded: bool


MAX_UNIVERSE_TWAK_QUOTES = 2


class LocalCache:
    """Simple JSON file cache for time-series data."""

    def __init__(self, filename: str) -> None:
        self.path = Path(filename)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass

    def add_data_point(self, symbol: str, value: float, max_age_hours: float) -> None:
        now = time.time()
        if symbol not in self.data:
            self.data[symbol] = []
        self.data[symbol].append({"timestamp": now, "value": value})

        cutoff = now - (max_age_hours * 3600)
        self.data[symbol] = [pt for pt in self.data[symbol] if pt["timestamp"] >= cutoff]

    def get_max_value(self, symbol: str, max_age_hours: float | None = None) -> float | None:
        points = self.data.get(symbol, [])
        if max_age_hours is not None:
            cutoff = time.time() - (max_age_hours * 3600)
            points = [pt for pt in points if pt.get("timestamp", 0) >= cutoff]
        values = [value for point in points if (value := self._point_value(point)) is not None]
        if not values:
            return None
        return max(values)

    def get_window_highs(self, symbol: str, windows_hours: tuple[int, ...]) -> dict[int, float | None]:
        return {window: self.get_max_value(symbol, max_age_hours=window) for window in windows_hours}

    def get_oldest_value(self, symbol: str, max_age_hours: float | None = None) -> float | None:
        points = self.data.get(symbol, [])
        if max_age_hours is not None:
            cutoff = time.time() - (max_age_hours * 3600)
            points = [pt for pt in points if pt.get("timestamp", 0) >= cutoff]
        oldest: dict[str, Any] | None = None
        for point in points:
            if self._point_value(point) is None:
                continue
            if oldest is None or float(point.get("timestamp", 0)) < float(oldest.get("timestamp", 0)):
                oldest = point
        return self._point_value(oldest) if oldest is not None else None

    def get_average_value(self, symbol: str) -> float | None:
        points = self.data.get(symbol, [])
        values = [value for point in points if (value := self._point_value(point)) is not None]
        if not values:
            return None
        return sum(values) / len(values)

    @staticmethod
    def _point_value(point: dict[str, Any]) -> float | None:
        try:
            return float(point.get("value", point.get("price")))
        except (TypeError, ValueError):
            return None


class BreakoutEngine:
    """Evaluate BSC tokens against a 4-factor core entry filter."""

    def __init__(
        self,
        settings: Settings,
        twak_interface: TWAKInterface | None = None,
    ) -> None:
        self.settings = settings
        self.twak_interface = twak_interface or TWAKInterface()
        self.price_cache = LocalCache("price_cache.json")
        self.volume_cache = LocalCache("volume_cache.json")
        self.macro_cache = LocalCache("macro_cache.json")
        self._macro_context_results: dict[tuple[float | None, float | None, float | None], tuple[float, float]] = {}
        self._missing_factor_warnings: set[tuple[str, str]] = set()

    def evaluate_token(
        self,
        token_data: dict[str, Any],
        portfolio_value_usdc: float,
    ) -> BreakoutDecision:
        """Evaluate one token against the entry filter."""

        symbol = str(token_data.get("symbol", "")).upper()
        if not is_liquid({"symbol": symbol, **token_data}):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="token failed liquidity filter",
            )
        if not is_tradable_symbol(symbol):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="symbol outside tradable target allowlist",
            )
        if not is_momentum_candidate_symbol(symbol):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="symbol excluded from momentum candidates",
            )

        candidate = self._evaluate_cheap_candidate(token_data, portfolio_value_usdc)
        entry_score = self._entry_score(candidate, momentum_z_score=0.0)
        estimated_slippage: float | None = None
        if self._should_quote_candidate(candidate, entry_score):
            estimated_slippage = self._estimate_candidate_slippage(candidate)
        decision = self._decision_from_candidate(candidate, estimated_slippage, entry_score)
        self.price_cache.save()
        self.volume_cache.save()
        self.macro_cache.save()
        return decision

    def _evaluate_cheap_candidate(
        self,
        token_data: dict[str, Any],
        portfolio_value_usdc: float,
    ) -> _CheapCandidate:
        """Evaluate all candidate factors that do not require TWAK."""

        symbol = str(token_data.get("symbol", "")).upper()
        price = self._positive_number(token_data.get("price"))
        volume_24h = self._positive_number(token_data.get("volume_24h"))
        market_cap = self._positive_number(token_data.get("market_cap"))
        rsi = self._positive_number(token_data.get("rsi"))
        funding_rate = self._number(token_data.get("funding_rate"))
        open_interest_change = self._number(token_data.get("open_interest_change_pct"))

        volume_breakout, volume_surge_score = self._volume_signal(symbol, token_data, volume_24h, market_cap)

        breakout_profile = self._breakout_profile(symbol, token_data, price)
        macro_score, macro_size_multiplier = self._macro_context(token_data)

        regime_not_risk_off = self.check_regime(token_data)
        position_size = portfolio_value_usdc * self.settings.max_position_pct
        if not regime_not_risk_off:
            position_size *= float(getattr(self.settings, "regime_size_multiplier", 0.5))
        position_size *= macro_size_multiplier

        if rsi is None:
            self._warn_missing_factor_once(symbol, "rsi_in_range")
            rsi_in_range = False
        else:
            rsi_in_range = 55.0 <= rsi <= 75.0

        if funding_rate is None or open_interest_change is None:
            self._warn_missing_factor_once(symbol, "derivatives_risk_clear")
            derivatives_risk_clear = False
        else:
            derivatives_risk_clear = not (abs(funding_rate) > 0.0015 or open_interest_change < -10.0)

        six_hour_high_break = breakout_profile.six_hour_high_break
        cheap_core_pass_count = sum(
            1 for passed in (volume_breakout, six_hour_high_break) if passed
        )
        true_factor_count_without_slippage = sum(
            1
            for passed in (
                volume_breakout,
                six_hour_high_break,
                regime_not_risk_off,
                rsi_in_range,
                derivatives_risk_clear,
            )
            if passed
        )

        return _CheapCandidate(
            symbol=symbol,
            token_data=token_data,
            position_size_usdc=position_size,
            volume_24h=volume_24h or 0.0,
            volume_breakout=volume_breakout,
            six_hour_high_break=breakout_profile.six_hour_high_break,
            regime_not_risk_off=regime_not_risk_off,
            rsi_in_range=rsi_in_range,
            derivatives_risk_clear=derivatives_risk_clear,
            cheap_core_pass_count=cheap_core_pass_count,
            true_factor_count_without_slippage=true_factor_count_without_slippage,
            breakout_strength=breakout_profile.strength,
            volume_surge_score=volume_surge_score,
            macro_score=macro_score,
            macro_size_multiplier=macro_size_multiplier,
            broken_reference_high=breakout_profile.broken_reference_high,
            chase_cap_exceeded=breakout_profile.chase_cap_exceeded,
            momentum_1h=self._token_change_fraction(token_data, hours=1) or 0.0,
            momentum_24h=self._token_change_fraction(token_data, hours=24) or 0.0,
        )

    def _estimate_candidate_slippage(self, candidate: _CheapCandidate) -> float | None:
        try:
            return self.twak_interface.estimate_slippage_pct(
                amount=candidate.position_size_usdc,
                from_token=self.settings.default_stable_symbol,
                to_token=candidate.symbol,
            )
        except Exception as exc:
            LOGGER.warning("TWAK slippage quote failed for %s: %s", candidate.symbol, exc)
            return None

    def _decision_from_candidate(
        self,
        candidate: _CheapCandidate,
        estimated_slippage: float | None,
        entry_score: float | None = None,
    ) -> BreakoutDecision:
        """Build a full decision after optional TWAK slippage evaluation."""

        resolved_score = self._entry_score(candidate, momentum_z_score=0.0) if entry_score is None else entry_score
        slippage_under_cap = (
            estimated_slippage is not None
            and estimated_slippage >= 0
            and estimated_slippage < self.settings.max_slippage_pct
        )

        factor_scores = {
            "volume_breakout": candidate.volume_breakout,
            "six_hour_high_break": candidate.six_hour_high_break,
            "regime_not_risk_off": candidate.regime_not_risk_off,
            "slippage_under_cap": slippage_under_cap,
            "rsi_in_range": candidate.rsi_in_range,
            "derivatives_risk_clear": candidate.derivatives_risk_clear,
        }

        true_factor_count = sum(1 for passed in factor_scores.values() if passed)

        threshold = float(getattr(self.settings, "breakout_entry_score_min", 45.0))
        quote_floor = self._quote_score_floor
        should_enter = (
            resolved_score >= threshold
            and slippage_under_cap
            and not candidate.chase_cap_exceeded
        )

        if should_enter:
            reason = (
                f"entry score {resolved_score:.1f} >= {threshold:.1f}; "
                f"slippage under cap ({true_factor_count}/{TOTAL_FACTOR_COUNT} factors true)"
            )
        elif candidate.chase_cap_exceeded:
            reference = candidate.broken_reference_high
            if reference is None:
                reason = "anti-chase cap exceeded"
            else:
                reason = (
                    f"anti-chase cap: price above broken reference high by more than "
                    f"{float(getattr(self.settings, 'max_chase_pct', 0.04)) * 100:.1f}%"
                )
        elif resolved_score < quote_floor:
            reason = (
                f"entry score {resolved_score:.1f} below quote floor {quote_floor:.1f}"
            )
        elif not slippage_under_cap:
            reason = "slippage estimate missing, negative, or above cap"
        elif resolved_score < threshold:
            reason = (
                f"entry score {resolved_score:.1f} below threshold {threshold:.1f}"
            )
        else:
            reason = "entry blocked by scoring model"

        return BreakoutDecision(
            should_enter=should_enter,
            symbol=candidate.symbol,
            position_size_usdc=candidate.position_size_usdc if should_enter else 0.0,
            factor_scores=factor_scores,
            true_factor_count=true_factor_count,
            reason=reason,
            estimated_slippage_pct=estimated_slippage,
            entry_score=resolved_score,
            position_size_multiplier=candidate.macro_size_multiplier,
        )

    def evaluate_all(
        self,
        market_snapshot: dict[str, dict[str, Any]],
        portfolio_value_usdc: float,
    ) -> list[BreakoutDecision]:
        """Scan target symbols and return all slippage-confirmed entry decisions."""

        candidates: list[_CheapCandidate] = []
        best_decision: BreakoutDecision | None = None
        best_volume = -1.0
        saw_target_symbol = False
        for symbol, token_data in market_snapshot.items():
            if not is_tradable_symbol(symbol) or not is_momentum_candidate_symbol(symbol):
                continue
            saw_target_symbol = True
            enriched_data = {"symbol": symbol.upper(), **token_data}
            if not is_liquid(enriched_data):
                continue
            candidate = self._evaluate_cheap_candidate(enriched_data, portfolio_value_usdc)
            candidates.append(candidate)

            unquoted_decision = self._decision_from_candidate(candidate, estimated_slippage=None)
            if self._is_better_decision(unquoted_decision, candidate.volume_24h, best_decision, best_volume):
                best_decision = unquoted_decision
                best_volume = candidate.volume_24h

        momentum_scores = self._momentum_z_scores(candidates)
        scores_by_symbol = {
            candidate.symbol: self._entry_score(
                candidate,
                momentum_z_score=momentum_scores.get(candidate.symbol, 0.0),
            )
            for candidate in candidates
        }
        if best_decision is not None and best_decision.symbol is not None:
            best_symbol = best_decision.symbol.upper()
            best_candidate = next((candidate for candidate in candidates if candidate.symbol == best_symbol), None)
            if best_candidate is not None:
                best_decision = self._decision_from_candidate(
                    best_candidate,
                    estimated_slippage=None,
                    entry_score=scores_by_symbol.get(best_symbol),
                )
        quote_candidates = sorted(
            (
                candidate
                for candidate in candidates
                if self._should_quote_candidate(candidate, scores_by_symbol.get(candidate.symbol, 0.0))
            ),
            key=lambda candidate: (
                scores_by_symbol.get(candidate.symbol, 0.0),
                candidate.breakout_strength,
                momentum_scores.get(candidate.symbol, 0.0),
                candidate.volume_24h,
            ),
            reverse=True,
        )

        passers: list[BreakoutDecision] = []
        for candidate in quote_candidates[:MAX_UNIVERSE_TWAK_QUOTES]:
            decision = self._decision_from_candidate(
                candidate,
                self._estimate_candidate_slippage(candidate),
                scores_by_symbol.get(candidate.symbol, 0.0),
            )
            if decision.should_enter:
                passers.append(decision)
            if self._is_better_decision(decision, candidate.volume_24h, best_decision, best_volume):
                best_decision = decision
                best_volume = candidate.volume_24h

        self.price_cache.save()
        self.volume_cache.save()
        self.macro_cache.save()

        if passers:
            return passers

        if best_decision is None:
            return [
                BreakoutDecision(
                    should_enter=False,
                    symbol=None,
                    position_size_usdc=0.0,
                    factor_scores={},
                    true_factor_count=0,
                    reason="no liquid target symbols available" if saw_target_symbol else "no target symbols available",
                )
            ]
        return [best_decision]

    def evaluate_universe(
        self,
        market_snapshot: dict[str, dict[str, Any]],
        portfolio_value_usdc: float,
    ) -> BreakoutDecision:
        """Scan target symbols and pick the highest-scoring candidate."""

        decisions = self.evaluate_all(market_snapshot, portfolio_value_usdc)
        passers = [decision for decision in decisions if decision.should_enter]
        if passers:
            return passers[0]
        return decisions[0]

    @property
    def _quote_score_floor(self) -> float:
        threshold = float(getattr(self.settings, "breakout_entry_score_min", 45.0))
        buffer = max(0.0, float(getattr(self.settings, "breakout_quote_score_buffer", 5.0)))
        return max(0.0, threshold - buffer)

    def _should_quote_candidate(self, candidate: _CheapCandidate, entry_score: float) -> bool:
        return not candidate.chase_cap_exceeded and entry_score >= self._quote_score_floor

    def _entry_score(self, candidate: _CheapCandidate, momentum_z_score: float) -> float:
        score = 0.0
        score += self._score_weight("breakout") * self._clamp01(candidate.breakout_strength)
        score += self._score_weight("volume") * self._clamp01(candidate.volume_surge_score)
        score += self._score_weight("momentum") * self._momentum_component(momentum_z_score)
        score += self._score_weight("rsi") * (1.0 if candidate.rsi_in_range else 0.0)
        score += self._score_weight("derivatives") * (1.0 if candidate.derivatives_risk_clear else 0.0)
        score += self._score_weight("macro") * self._clamp01(candidate.macro_score)
        return round(score, 4)

    def _score_weight(self, name: str) -> float:
        return max(0.0, float(getattr(self.settings, f"breakout_score_weight_{name}", 0.0) or 0.0))

    @staticmethod
    def _momentum_component(momentum_z_score: float) -> float:
        return BreakoutEngine._clamp01(momentum_z_score / 2.0)

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _reference_windows(self) -> tuple[int, ...]:
        raw = getattr(self.settings, "breakout_reference_windows_hours", list(DEFAULT_REFERENCE_WINDOWS_HOURS))
        windows = {int(window) for window in raw if int(window) > 0}
        windows.add(6)
        return tuple(sorted(windows))

    @property
    def _max_reference_window_hours(self) -> int:
        return max(self._reference_windows() or DEFAULT_REFERENCE_WINDOWS_HOURS)

    def _volume_breakout(
        self,
        symbol: str,
        token_data: dict[str, Any],
        volume_24h: float | None,
        market_cap: float | None,
    ) -> bool:
        return self._volume_signal(symbol, token_data, volume_24h, market_cap)[0]

    def _volume_signal(
        self,
        symbol: str,
        token_data: dict[str, Any],
        volume_24h: float | None,
        market_cap: float | None,
    ) -> tuple[bool, float]:
        volume_1h = self._positive_number(token_data.get("volume_1h"))
        rolling_hourly_avg = self._positive_number(token_data.get("rolling_24h_hourly_volume_avg"))
        if rolling_hourly_avg is None and volume_1h is not None and volume_24h is not None:
            rolling_hourly_avg = volume_24h / 24.0
        breakout_mult = self.settings.ml_volume_breakout_multiplier
        cache_mult = self.settings.ml_volume_cache_multiplier
        if volume_1h is not None and rolling_hourly_avg is not None and rolling_hourly_avg > 0:
            ratio = volume_1h / rolling_hourly_avg
            return ratio > breakout_mult, self._clamp01(ratio / breakout_mult)

        if volume_24h is not None:
            avg_vol = self.volume_cache.get_average_value(symbol)
            self.volume_cache.add_data_point(symbol, volume_24h, max_age_hours=24)
            if avg_vol is not None and avg_vol > 0:
                ratio = volume_24h / avg_vol
                return ratio > cache_mult, self._clamp01(ratio / cache_mult)
            if market_cap is not None and market_cap > 0:
                reference = 0.05 * market_cap
                ratio = volume_24h / reference if reference > 0 else 0.0
                return volume_24h > reference, self._clamp01(ratio)
        return False, 0.0

    def _breakout_high_break(
        self,
        symbol: str,
        token_data: dict[str, Any],
        price: float | None,
    ) -> bool:
        return self._breakout_profile(symbol, token_data, price).six_hour_high_break

    def _breakout_profile(
        self,
        symbol: str,
        token_data: dict[str, Any],
        price: float | None,
    ) -> _BreakoutProfile:
        if price is None:
            return _BreakoutProfile(False, 0.0, None, False)

        buffer_multiplier = 1.0 + self.settings.breakout_buffer
        windows = self._reference_windows()
        cached_highs = self.price_cache.get_window_highs(symbol, windows)
        references: dict[int, float] = {}
        cleared: dict[int, float] = {}
        for window in windows:
            fallback = self._positive_number(token_data.get(f"high_{window}h"))
            reference = cached_highs.get(window) if cached_highs.get(window) is not None else fallback
            if reference is None:
                continue
            references[window] = reference
            if price > reference * buffer_multiplier:
                cleared[window] = reference

        total_weight = sum(references) or 0
        cleared_weight = sum(window for window in cleared)
        strength = cleared_weight / total_weight if total_weight > 0 else 0.0
        six_reference = references.get(6)
        six_hour_high_break = six_reference is not None and price > six_reference * buffer_multiplier
        broken_reference_high = max(cleared.values()) if cleared else None
        max_chase_pct = float(getattr(self.settings, "max_chase_pct", 0.04))
        chase_cap_exceeded = (
            broken_reference_high is not None
            and max_chase_pct >= 0
            and price > broken_reference_high * (1.0 + max_chase_pct)
        )

        self.price_cache.add_data_point(symbol, price, max_age_hours=self._max_reference_window_hours)
        return _BreakoutProfile(
            six_hour_high_break=six_hour_high_break,
            strength=strength,
            broken_reference_high=broken_reference_high,
            chase_cap_exceeded=chase_cap_exceeded,
        )

    def _macro_context(self, token_data: dict[str, Any]) -> tuple[float, float]:
        total_market_cap = self._positive_number(token_data.get("macro_total_market_cap"))
        btc_dominance = self._number(token_data.get("macro_btc_dominance"))
        stablecoin_dominance = self._number(token_data.get("macro_stablecoin_dominance"))
        if total_market_cap is None and btc_dominance is None and stablecoin_dominance is None:
            return 0.0, 1.0
        cache_key = (total_market_cap, btc_dominance, stablecoin_dominance)
        cached = self._macro_context_results.get(cache_key)
        if cached is not None:
            return cached

        score = 0.0
        observed = 0
        total_delta = self._macro_delta("TOTAL_MARKET_CAP", total_market_cap)
        btc_delta = self._macro_delta("BTC_DOMINANCE", btc_dominance)
        stable_delta = self._macro_delta("STABLECOIN_DOMINANCE", stablecoin_dominance)

        if total_delta is not None:
            observed += 1
            score += 0.4 if total_delta >= 0 else 0.0
        if btc_delta is not None:
            observed += 1
            score += 0.3 if btc_delta <= 0.25 else 0.0
        if stable_delta is not None:
            observed += 1
            score += 0.3 if stable_delta <= 0 else 0.0
        if observed == 0:
            result = (0.0, 1.0)
            self._macro_context_results[cache_key] = result
            return result
        score = self._clamp01(score)
        result = (score, 0.5 + 0.5 * score)
        self._macro_context_results[cache_key] = result
        return result

    def _macro_delta(self, key: str, current: float | None) -> float | None:
        if current is None:
            return None
        oldest = self.macro_cache.get_oldest_value(key, max_age_hours=24)
        self.macro_cache.add_data_point(key, current, max_age_hours=24)
        if oldest is None:
            return None
        return current - oldest

    def _warn_missing_factor_once(self, symbol: str, factor: str) -> None:
        key = (symbol.upper(), factor)
        if key in self._missing_factor_warnings:
            return
        self._missing_factor_warnings.add(key)
        LOGGER.warning("Missing data for %s factor on %s; failing factor closed", factor, symbol)

    def check_regime(self, token_data: dict[str, Any], bnb_data: dict[str, Any] | None = None) -> bool:
        bnb_source = bnb_data if bnb_data is not None else token_data
        bnb_change_1h = self._bnb_change_1h_fraction(bnb_source, separate_bnb_data=bnb_data is not None)
        token_change_1h = self._token_change_fraction(token_data, hours=1)
        token_change_24h = self._token_change_fraction(token_data, hours=24)
        bnb_ok = bnb_change_1h is not None and bnb_change_1h > self.settings.bnb_regime_threshold
        token_1h_ok = token_change_1h is not None and token_change_1h > self.settings.token_regime_1h_min
        token_24h_ok = token_change_24h is not None and token_change_24h > self.settings.token_regime_24h_min
        return bnb_ok and token_1h_ok and token_24h_ok

    def _bnb_change_1h_fraction(self, data: dict[str, Any], separate_bnb_data: bool) -> float | None:
        if separate_bnb_data:
            return self._first_change_fraction(
                data,
                (
                    ("percent_change_1h", "fraction"),
                    ("bnb_percent_change_1h", "fraction"),
                    ("price_change_percentage_1h", "percent_points"),
                    ("change_1h", "percent_points"),
                    ("bnb_1h_trend_pct", "percent_points"),
                ),
            )
        return self._first_change_fraction(
            data,
            (
                ("bnb_percent_change_1h", "fraction"),
                ("bnb_1h_trend_pct", "percent_points"),
                ("bnb_1h_change_pct", "percent_points"),
            ),
        )

    def _token_change_fraction(self, data: dict[str, Any], hours: int) -> float | None:
        return self._first_change_fraction(
            data,
            (
                (f"token_percent_change_{hours}h", "fraction"),
                (f"token_change_{hours}h", "fraction"),
                (f"percent_change_{hours}h", "percent_points"),
                (f"price_change_percentage_{hours}h", "percent_points"),
                (f"change_{hours}h", "percent_points"),
            ),
        )

    def _first_change_fraction(
        self,
        data: dict[str, Any],
        fields: tuple[tuple[str, str], ...],
    ) -> float | None:
        for key, mode in fields:
            number = self._number(data.get(key))
            if number is None:
                continue
            if mode == "percent_points":
                return number / 100.0
            return number
        return None

    @staticmethod
    def _cheap_candidate_rank(candidate: _CheapCandidate) -> tuple[int, int, float]:
        return (
            candidate.cheap_core_pass_count,
            candidate.true_factor_count_without_slippage,
            candidate.volume_24h,
        )

    @staticmethod
    def _momentum_z_scores(candidates: list[_CheapCandidate]) -> dict[str, float]:
        """Cross-sectional momentum z-score per symbol: z(1h) + 0.5 * z(24h).

        Replaces raw 24h volume as the quote-priority tiebreak so the freshest
        movers, not just the largest tokens, win the limited TWAK quote slots.
        Falls back to 0.0 for all symbols when the candidate set is too small
        or has zero dispersion (volume tiebreak then decides).
        """

        if len(candidates) < 2:
            return {}

        def z_scores(values: list[float]) -> list[float]:
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            std = variance**0.5
            if std <= 0.0:
                return [0.0] * len(values)
            return [(value - mean) / std for value in values]

        z_1h = z_scores([candidate.momentum_1h for candidate in candidates])
        z_24h = z_scores([candidate.momentum_24h for candidate in candidates])
        return {
            candidate.symbol: z_1h[index] + 0.5 * z_24h[index]
            for index, candidate in enumerate(candidates)
        }

    @staticmethod
    def _is_better_decision(
        candidate: BreakoutDecision,
        candidate_volume: float,
        best: BreakoutDecision | None,
        best_volume: float,
    ) -> bool:
        if best is None:
            return True
        candidate_score = candidate.entry_score if candidate.entry_score is not None else -1.0
        best_score = best.entry_score if best.entry_score is not None else -1.0
        if candidate_score > best_score:
            return True
        if candidate_score < best_score:
            return False
        if candidate.true_factor_count > best.true_factor_count:
            return True
        return candidate.true_factor_count == best.true_factor_count and candidate_volume > best_volume

    @staticmethod
    def _positive_number(value: Any) -> float | None:
        number = BreakoutEngine._number(value)
        if number is None or number <= 0:
            return None
        return number

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
