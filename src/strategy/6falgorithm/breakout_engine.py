"""Six-factor momentum breakout strategy engine adapted to 4-factor core."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config.settings import Settings
from src.config.tokens import (
    has_verified_bsc_contract,
    is_liquid,
    is_momentum_candidate_symbol,
    is_tradable_symbol,
)
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
    # Provenance of the slippage figure so observers can tell apart a candidate
    # that was never sent for a TWAK quote ("not_quoted") from one that was
    # quoted but the quote failed/returned nothing ("failed") from a real
    # numeric quote ("quoted"). Both former cases leave estimated_slippage_pct
    # None, so without this they look identical on the dashboard.
    slippage_quote_state: str = "not_quoted"
    # Raw measured value behind each factor (human-readable), so the dashboard can
    # display the real numbers the booleans were derived from. Keyed by factor name.
    factor_metrics: dict[str, str] = field(default_factory=dict)
    ml_context: Any | None = None
    ml_audit: dict[str, Any] | None = None
    # Quality-guard results for telemetry. None when no guard was evaluated.
    quality_guards: dict[str, bool] | None = None
    entries_blocked_reason: str | None = None


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
    atr_ratio: float = 1.0
    # Raw indicator readings retained for telemetry/audit display.
    rsi: float | None = None
    funding_rate: float | None = None
    open_interest_change: float | None = None
    price: float | None = None
    last_reference_high: float | None = None
    derivatives_score: float = 0.5
    atr_pass: bool = True
    momentum_7d: float = 0.0
    momentum_30d: float = 0.0
    momentum_90d: float = 0.0
    cmc_rank: int | None = None
    watchlist_count: int | None = None
    circulating_supply: float | None = None



@dataclass(frozen=True)
class _BreakoutProfile:
    six_hour_high_break: bool
    strength: float
    broken_reference_high: float | None
    chase_cap_exceeded: bool


MAX_UNIVERSE_TWAK_QUOTES = 4  # was 2


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
        sentiment_tier1: Any | None = None,
    ) -> None:
        self.settings = settings
        self.twak_interface = twak_interface or TWAKInterface()
        self.sentiment_tier1 = sentiment_tier1
        self.price_cache = LocalCache("price_cache.json")
        self.volume_cache = LocalCache("volume_cache.json")
        self.macro_cache = LocalCache("macro_cache.json")
        self.funding_cache = LocalCache("funding_cache.json")
        self._macro_context_results: dict[tuple[float | None, float | None, float | None, float | None, float | None], tuple[float, float]] = {}

        self._missing_factor_warnings: set[tuple[str, str]] = set()
        self._last_momentum_z_scores: dict[str, float] = {}

    def evaluate_token(
        self,
        token_data: dict[str, Any],
        portfolio_value_usdc: float,
        ml_context: Any | None = None,
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
                ml_context=ml_context,
            )
        if not is_tradable_symbol(symbol):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="symbol outside tradable target allowlist",
                ml_context=ml_context,
            )
        if not is_momentum_candidate_symbol(symbol):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="symbol excluded from momentum candidates",
                ml_context=ml_context,
            )
        if bool(getattr(self.settings, "require_verified_bsc_contract", True)) and not has_verified_bsc_contract(symbol):
            return BreakoutDecision(
                should_enter=False,
                symbol=symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="symbol has no verified BSC contract (not executable on TWAK)",
                ml_context=ml_context,
            )

        candidate = self._evaluate_cheap_candidate(token_data, portfolio_value_usdc)
        if not candidate.atr_pass:
            return BreakoutDecision(
                should_enter=False,
                symbol=candidate.symbol or None,
                position_size_usdc=0.0,
                factor_scores={},
                true_factor_count=0,
                reason="ATR below mean — low volatility regime blocked",
                ml_context=ml_context,
            )
        token_sentiment: dict[str, Any] = {}
        if self.sentiment_tier1 is not None:
            try:
                token_sentiment = self.sentiment_tier1.get_token_sentiment(candidate.symbol)
            except Exception:
                token_sentiment = {}
        cached_z = self._last_momentum_z_scores.get(candidate.symbol, 0.0)
        entry_score = self._entry_score(candidate, momentum_z_score=cached_z, token_sentiment=token_sentiment, atr_ratio=candidate.atr_ratio)

        estimated_slippage: float | None = None
        quote_state = "not_quoted"
        if self._should_quote_candidate(candidate, entry_score):
            estimated_slippage, quote_state = self._estimate_candidate_slippage(candidate)
        decision = self._decision_from_candidate(
            candidate, estimated_slippage, entry_score, slippage_quote_state=quote_state, ml_context=ml_context
        )
        self.price_cache.save()
        self.volume_cache.save()
        self.macro_cache.save()
        self.funding_cache.save()
        return decision

    def _evaluate_cheap_candidate(
        self,
        token_data: dict[str, Any],
        portfolio_value_usdc: float,
        bnb_data: dict[str, Any] | None = None,
    ) -> _CheapCandidate:
        """Evaluate all candidate factors that do not require TWAK."""

        symbol = str(token_data.get("symbol", "")).upper()
        atr_pass, atr_ratio = self._atr_regime(symbol)
        price = self._positive_number(token_data.get("price"))
        volume_24h = self._positive_number(token_data.get("volume_24h"))
        market_cap = self._positive_number(token_data.get("market_cap"))
        rsi = self._positive_number(token_data.get("rsi"))
        funding_rate = self._number(token_data.get("funding_rate"))
        open_interest_change = self._number(token_data.get("open_interest_change_pct"))
        momentum_7d = self._number(token_data.get("percent_change_7d")) or 0.0
        momentum_30d = self._number(token_data.get("percent_change_30d")) or 0.0
        momentum_90d = self._number(token_data.get("percent_change_90d")) or 0.0
        cmc_rank = self._number(token_data.get("cmc_rank"))
        cmc_rank = int(cmc_rank) if cmc_rank is not None else None
        watchlist_count = self._number(token_data.get("watchlist_count"))
        watchlist_count = int(watchlist_count) if watchlist_count is not None else None
        circulating_supply = self._positive_number(token_data.get("circulating_supply"))

        volume_breakout, volume_surge_score = self._volume_signal(symbol, token_data, volume_24h, market_cap)

        breakout_profile = self._breakout_profile(symbol, token_data, price)
        macro_score, macro_size_multiplier = self._macro_context(token_data)

        regime_not_risk_off = self.check_regime(token_data, bnb_data)
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
            # CMC has no funding/OI feed, so this data is structurally absent.
            # Default is fail-closed; when derivatives_neutral_on_missing is set,
            # treat absent data as neutral (pass) so a metric we cannot source
            # stops capping every candidate at 5/6. Present data is still strict.
            if bool(getattr(self.settings, "derivatives_neutral_on_missing", False)):
                derivatives_risk_clear = True
            else:
                self._warn_missing_factor_once(symbol, "derivatives_risk_clear")
                derivatives_risk_clear = False
            derivatives_score = 0.5
        else:
            derivatives_risk_clear = not (abs(funding_rate) > 0.0015 or open_interest_change < -10.0)
            derivatives_score = 1.0 if derivatives_risk_clear else 0.0

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
            rsi=rsi,
            funding_rate=funding_rate,
            open_interest_change=open_interest_change,
            price=price,
            last_reference_high=breakout_profile.broken_reference_high,
            derivatives_score=derivatives_score,
            atr_pass=atr_pass,
            atr_ratio=atr_ratio,
            momentum_7d=momentum_7d,
            momentum_30d=momentum_30d,
            momentum_90d=momentum_90d,
            cmc_rank=cmc_rank,
            watchlist_count=watchlist_count,
            circulating_supply=circulating_supply,
        )

    def _compute_atr_14(self, symbol: str) -> tuple[float | None, float | None]:
        """Compute 14-period ATR and 20-period mean from price_cache."""
        points = self.price_cache.data.get(symbol, [])
        if len(points) < 14:
            return None, None
        # Sort by timestamp
        sorted_points = sorted(points, key=lambda p: p.get("timestamp", 0))
        prices = [self.price_cache._point_value(p) for p in sorted_points]
        prices = [p for p in prices if p is not None]
        if len(prices) < 14:
            return None, None
        # True Range approximation: |price[i] - price[i-1]|
        tr_values = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        atr_14 = sum(tr_values[-14:]) / 14.0
        atr_mean = sum(tr_values[-20:]) / min(20, len(tr_values)) if tr_values else None
        return atr_14, atr_mean

    def _atr_regime(self, symbol: str) -> tuple[bool, float]:
        """Return (pass, atr_ratio). Pass if ATR >= 20-period mean."""
        atr_14, atr_mean = self._compute_atr_14(symbol)
        if atr_14 is None or atr_mean is None or atr_mean <= 0:
            return True, 1.0  # fail-open if data missing
        atr_ratio = atr_14 / atr_mean
        return atr_ratio >= 1.0, atr_ratio

    def _estimate_candidate_slippage(self, candidate: _CheapCandidate) -> tuple[float | None, str]:
        """Quote slippage and report whether the quote succeeded.

        Returns ``(value, state)`` where state is ``"quoted"`` when a usable
        numeric slippage came back, or ``"failed"`` when the quote raised or
        returned nothing. The caller uses ``"not_quoted"`` for candidates that
        were never sent for a quote at all, so the three cases stay distinct in
        telemetry instead of collapsing into a single ``None``.
        """

        try:
            value = self.twak_interface.estimate_slippage_pct(
                amount=candidate.position_size_usdc,
                from_token=self.settings.default_stable_symbol,
                to_token=candidate.symbol,
            )
        except Exception as exc:
            LOGGER.warning("TWAK slippage quote failed for %s: %s", candidate.symbol, exc)
            return None, "failed"
        if value is None:
            LOGGER.warning("TWAK slippage quote returned no value for %s", candidate.symbol)
            return None, "failed"
        return value, "quoted"

    def _decision_from_candidate(
        self,
        candidate: _CheapCandidate,
        estimated_slippage: float | None,
        entry_score: float | None = None,
        slippage_quote_state: str = "not_quoted",
        ml_context: Any | None = None,
        token_sentiment: dict[str, Any] | None = None,
    ) -> BreakoutDecision:
        """Build a full decision after optional TWAK slippage evaluation."""

        resolved_score = self._entry_score(candidate, momentum_z_score=0.0, token_sentiment=token_sentiment, atr_ratio=candidate.atr_ratio) if entry_score is None else entry_score
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

        quality_guards: dict[str, bool] = {
            "score_above_threshold": should_enter or resolved_score >= threshold,
            "slippage_under_cap": slippage_under_cap,
            "chase_cap_ok": not candidate.chase_cap_exceeded,
        }
        entries_blocked_reason: str | None = None

        if should_enter:
            should_enter, entries_blocked_reason, quality_guards = self._apply_quality_guards(
                candidate,
                resolved_score,
                threshold,
                true_factor_count,
                ml_context,
                quality_guards,
            )

        if should_enter:
            reason = (
                f"entry score {resolved_score:.1f} >= {threshold:.1f}; "
                f"slippage under cap ({true_factor_count}/{TOTAL_FACTOR_COUNT} factors true)"
            )
        elif entries_blocked_reason is not None:
            reason = entries_blocked_reason
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
            if slippage_quote_state == "not_quoted":
                reason = "slippage not quoted (candidate not sent for a TWAK quote yet)"
            elif slippage_quote_state == "failed":
                reason = "slippage quote failed (TWAK returned no usable quote)"
            elif estimated_slippage is not None and estimated_slippage < 0:
                reason = "slippage estimate negative"
            elif estimated_slippage is not None:
                reason = (
                    f"slippage {estimated_slippage * 100:.2f}% above cap "
                    f"{self.settings.max_slippage_pct * 100:.2f}%"
                )
            else:
                reason = "slippage estimate missing, negative, or above cap"
        elif resolved_score < threshold:
            reason = (
                f"entry score {resolved_score:.1f} below threshold {threshold:.1f}"
            )
        else:
            reason = "entry blocked by scoring model"

        factor_metrics = self._build_factor_metrics(
            candidate, estimated_slippage, resolved_score, slippage_quote_state
        )

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
            factor_metrics=factor_metrics,
            slippage_quote_state=slippage_quote_state,
            ml_context=ml_context,
            quality_guards=quality_guards,
            entries_blocked_reason=entries_blocked_reason,
        )

    def _apply_quality_guards(
        self,
        candidate: _CheapCandidate,
        resolved_score: float,
        threshold: float,
        true_factor_count: int,
        ml_context: Any | None,
        quality_guards: dict[str, bool],
    ) -> tuple[bool, str | None, dict[str, bool]]:
        """Apply ML-aware and rule-based quality gates after the base score clears.

        Returns (should_enter, blocked_reason, updated_quality_guards).
        """

        settings = self.settings
        min_factor_count = int(getattr(settings, "breakout_min_true_factor_count", 0))
        block_risk_off = bool(getattr(settings, "breakout_block_in_risk_off_regime", False))
        require_rsi = bool(getattr(settings, "breakout_require_rsi_in_range", False))
        score_buffer = float(getattr(settings, "breakout_min_entry_score_buffer", 0.0))
        ml_min_confidence = float(getattr(settings, "breakout_ml_min_confidence", 0.0))
        block_chop = bool(getattr(settings, "breakout_block_in_chop_regime", False))
        chop_buffer = float(getattr(settings, "breakout_chop_confidence_buffer", 0.0))

        # ML-aware guards (only when an ML context is provided).
        if ml_context is not None:
            ml_confidence = float(getattr(ml_context, "confidence", 0.0) or 0.0)
            ml_regime = getattr(ml_context, "regime", None)
            quality_guards["ml_confidence_ok"] = ml_min_confidence <= 0 or ml_confidence >= ml_min_confidence
            quality_guards["ml_chop_ok"] = not block_chop or ml_regime != "chop" or ml_confidence >= ml_min_confidence + chop_buffer

            if ml_min_confidence > 0 and ml_confidence < ml_min_confidence:
                return False, f"ML confidence {ml_confidence:.3f} below minimum {ml_min_confidence:.3f}", quality_guards
            if block_chop and ml_regime == "chop" and ml_confidence < ml_min_confidence + chop_buffer:
                return False, f"chop regime (confidence {ml_confidence:.3f})", quality_guards

        # Rule-based guards (always applied; these are the fail-closed floor).
        quality_guards["factor_count_ok"] = min_factor_count <= 0 or true_factor_count >= min_factor_count
        quality_guards["risk_off_ok"] = not block_risk_off or candidate.regime_not_risk_off
        quality_guards["rsi_ok"] = not require_rsi or candidate.rsi_in_range
        quality_guards["score_buffer_ok"] = score_buffer <= 0 or resolved_score >= threshold + score_buffer

        if block_risk_off and not candidate.regime_not_risk_off:
            return False, "rule-based regime is risk-off", quality_guards
        if min_factor_count > 0 and true_factor_count < min_factor_count:
            return False, f"only {true_factor_count}/{TOTAL_FACTOR_COUNT} entry factors passed (min {min_factor_count})", quality_guards
        if require_rsi and not candidate.rsi_in_range:
            return False, "RSI missing or outside 55–75 band", quality_guards
        if score_buffer > 0 and resolved_score < threshold + score_buffer:
            return False, f"entry score {resolved_score:.1f} below buffered threshold {threshold + score_buffer:.1f}", quality_guards

        return True, None, quality_guards

    def _build_factor_metrics(
        self,
        candidate: _CheapCandidate,
        estimated_slippage: float | None,
        entry_score: float | None,
        slippage_quote_state: str = "not_quoted",
    ) -> dict[str, str]:
        """Human-readable reading behind each factor for dashboard display.

        These are the actual measured values the boolean gates were derived from,
        so an observer can cross-check the agent against live market data.
        """

        def price_fmt(value: float | None) -> str:
            if value is None:
                return "n/a"
            return f"{value:,.6g}"

        cap_pct = float(getattr(self.settings, "max_slippage_pct", 0.0)) * 100.0
        metrics: dict[str, str] = {}

        metrics["volume_breakout"] = (
            f"surge {candidate.volume_surge_score:.2f}× · 24h vol ${candidate.volume_24h:,.0f}"
        )

        ref_high = candidate.last_reference_high
        if candidate.six_hour_high_break and ref_high is not None:
            metrics["six_hour_high_break"] = (
                f"price {price_fmt(candidate.price)} cleared 6h high {price_fmt(ref_high)}"
            )
        elif ref_high is not None:
            metrics["six_hour_high_break"] = (
                f"price {price_fmt(candidate.price)} · 6h high {price_fmt(ref_high)}"
            )
        else:
            metrics["six_hour_high_break"] = f"price {price_fmt(candidate.price)} · no 6h reference"

        metrics["regime_not_risk_off"] = (
            "regime risk-on / neutral" if candidate.regime_not_risk_off else "regime risk-off"
        )

        if estimated_slippage is None:
            if slippage_quote_state == "failed":
                metrics["slippage_under_cap"] = f"quote failed · cap {cap_pct:.2f}%"
            else:
                metrics["slippage_under_cap"] = f"not quoted · cap {cap_pct:.2f}%"
        else:
            metrics["slippage_under_cap"] = (
                f"{estimated_slippage * 100:.2f}% · cap {cap_pct:.2f}%"
            )

        if candidate.rsi is None:
            metrics["rsi_in_range"] = "RSI n/a · band 55–75"
        else:
            metrics["rsi_in_range"] = f"RSI {candidate.rsi:.1f} · band 55–75"

        if candidate.funding_rate is None or candidate.open_interest_change is None:
            if bool(getattr(self.settings, "derivatives_neutral_on_missing", False)):
                metrics["derivatives_risk_clear"] = "funding/OI data missing · neutral (pass)"
            else:
                metrics["derivatives_risk_clear"] = "funding/OI data missing"
        else:
            metrics["derivatives_risk_clear"] = (
                f"funding {candidate.funding_rate * 100:.3f}% · OI {candidate.open_interest_change:+.1f}%"
            )

        if entry_score is not None:
            metrics["entry_score"] = (
                f"{entry_score:.1f}/100 · need {float(getattr(self.settings, 'breakout_entry_score_min', 45.0)):.0f}+"
                f" · floor {self._quote_score_floor:.0f}"
            )

        return metrics

    def evaluate_all(
        self,
        market_snapshot: dict[str, dict[str, Any]],
        portfolio_value_usdc: float,
        ml_contexts: dict[str, Any] | None = None,
    ) -> list[BreakoutDecision]:
        """Scan target symbols and return all slippage-confirmed entry decisions."""

        ml_contexts = ml_contexts or {}
        bnb_reference = self._bnb_reference(market_snapshot)
        candidates: list[_CheapCandidate] = []
        best_decision: BreakoutDecision | None = None
        best_volume = -1.0
        saw_target_symbol = False
        require_contract = bool(getattr(self.settings, "require_verified_bsc_contract", True))
        for symbol, token_data in market_snapshot.items():
            if not is_tradable_symbol(symbol) or not is_momentum_candidate_symbol(symbol):
                continue
            # Skip symbols TWAK cannot execute (no verified BEP-20 contract): they
            # otherwise win the candidate ranking on score, then fail the quote and
            # leave the bot on WAIT instead of trading the best *executable* name.
            if require_contract and not has_verified_bsc_contract(symbol):
                continue
            saw_target_symbol = True
            enriched_data = {"symbol": symbol.upper(), **token_data}
            if not is_liquid(enriched_data):
                continue
            candidate = self._evaluate_cheap_candidate(
                enriched_data, portfolio_value_usdc, bnb_data=bnb_reference
            )
            if not candidate.atr_pass:
                continue
            candidates.append(candidate)

        # Fetch token sentiments in batch for candidates
        token_sentiments: dict[str, dict[str, Any]] = {}
        if self.sentiment_tier1 is not None:
            for candidate in candidates:
                try:
                    token_sentiments[candidate.symbol] = self.sentiment_tier1.get_token_sentiment(candidate.symbol)
                except Exception:
                    token_sentiments[candidate.symbol] = {}

        for candidate in candidates:
            unquoted_decision = self._decision_from_candidate(
                candidate,
                estimated_slippage=None,
                ml_context=ml_contexts.get(candidate.symbol),
                token_sentiment=token_sentiments.get(candidate.symbol, {}),
            )
            if self._is_better_decision(unquoted_decision, candidate.volume_24h, best_decision, best_volume):
                best_decision = unquoted_decision
                best_volume = candidate.volume_24h

        momentum_scores = self._momentum_z_scores(candidates)
        self._last_momentum_z_scores = momentum_scores
        scores_by_symbol = {
            candidate.symbol: self._entry_score(
                candidate,
                momentum_z_score=momentum_scores.get(candidate.symbol, 0.0),
                token_sentiment=token_sentiments.get(candidate.symbol, {}),
                atr_ratio=candidate.atr_ratio,
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
                    ml_context=ml_contexts.get(best_symbol),
                    token_sentiment=token_sentiments.get(best_symbol, {}),
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
            slippage_value, quote_state = self._estimate_candidate_slippage(candidate)
            decision = self._decision_from_candidate(
                candidate,
                slippage_value,
                scores_by_symbol.get(candidate.symbol, 0.0),
                slippage_quote_state=quote_state,
                ml_context=ml_contexts.get(candidate.symbol),
                token_sentiment=token_sentiments.get(candidate.symbol, {}),
            )
            if decision.should_enter:
                passers.append(decision)
            if self._is_better_decision(decision, candidate.volume_24h, best_decision, best_volume):
                best_decision = decision
                best_volume = candidate.volume_24h

        self.price_cache.save()
        self.volume_cache.save()
        self.macro_cache.save()
        self.funding_cache.save()
        self._log_factor_matrix(candidates, scores_by_symbol, momentum_scores, bnb_reference)

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
        ml_contexts: dict[str, Any] | None = None,
    ) -> BreakoutDecision:
        """Scan target symbols and pick the highest-scoring candidate."""

        decisions = self.evaluate_all(market_snapshot, portfolio_value_usdc, ml_contexts=ml_contexts)
        passers = [decision for decision in decisions if decision.should_enter]
        if passers:
            return passers[0]
        return decisions[0]

    @property
    def _quote_score_floor(self) -> float:
        threshold = float(getattr(self.settings, "breakout_entry_score_min", 45.0))
        return max(0.0, threshold + 3.0)

    def _should_quote_candidate(self, candidate: _CheapCandidate, entry_score: float) -> bool:
        return not candidate.chase_cap_exceeded and entry_score >= self._quote_score_floor

    def _entry_score(self, candidate: _CheapCandidate, momentum_z_score: float, token_sentiment: dict[str, Any] | None = None, atr_ratio: float = 1.0) -> float:
        weights = self._regime_adjusted_weights(atr_ratio)
        score = 0.0
        score += weights["breakout"] * self._clamp01(candidate.breakout_strength)
        score += weights["volume"] * self._clamp01(candidate.volume_surge_score)
        score += weights["momentum"] * self._momentum_component(momentum_z_score)
        score += weights["rsi"] * self._rsi_component(candidate.rsi)
        score += weights["derivatives"] * self._derivatives_component(candidate.funding_rate, candidate.open_interest_change)
        score += weights["macro"] * self._clamp01(candidate.macro_score)

        # Token-specific sentiment modifiers (CMC MCP news + narratives)
        if token_sentiment:
            if token_sentiment.get("news_bearish_last_4h"):
                score -= 10.0
            if token_sentiment.get("kol_bullish") and token_sentiment.get("funding_neutral"):
                score += 5.0
        return max(0.0, round(score, 4))

    def _score_weight(self, name: str) -> float:
        return max(0.0, float(getattr(self.settings, f"breakout_score_weight_{name}", 0.0) or 0.0))

    @staticmethod
    def _momentum_component(momentum_z_score: float) -> float:
        return BreakoutEngine._clamp01(momentum_z_score / 2.0)

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _rsi_component(self, rsi: float | None) -> float:
        """Smooth bell curve centered at 65. Zero at 45 and 85."""
        if rsi is None:
            return 0.0
        distance = abs(rsi - 65.0)
        if distance >= 20.0:
            return 0.0
        return 1.0 - (distance / 20.0)

    def _regime_adjusted_weights(self, atr_ratio: float) -> dict[str, float]:
        """Return weight dict summing to 100 based on ATR regime."""
        base = {
            "breakout": 35.0,
            "volume": 25.0,
            "momentum": 15.0,
            "rsi": 10.0,
            "derivatives": 10.0,
            "macro": 5.0,
        }
        if atr_ratio > 1.5:
            weights = {
                "breakout": 30.0,
                "volume": 20.0,
                "momentum": 25.0,
                "rsi": 5.0,
                "derivatives": 10.0,
                "macro": 10.0,
            }
        elif atr_ratio < 0.7:
            weights = {
                "breakout": 20.0,
                "volume": 40.0,
                "momentum": 10.0,
                "rsi": 15.0,
                "derivatives": 10.0,
                "macro": 5.0,
            }
        else:
            weights = dict(base)
        # Renormalize to sum 100
        total = sum(weights.values())
        if total != 100.0:
            weights = {k: v * 100.0 / total for k, v in weights.items()}
        return weights

    def _derivatives_component(self, funding_rate: float | None, oi_change: float | None) -> float:
        """Continuous derivatives score: 0.5 = neutral, 1.0 = favorable squeeze, 0.0 = overheated."""
        if funding_rate is None or oi_change is None:
            return 0.5  # neutral — no free pass, no penalty
        # Simplified funding z-score (normalized against 0.1% std)
        funding_z = funding_rate / 0.001
        # Extreme negative funding (shorts paying) = squeeze setup = high score
        funding_score = self._clamp01(0.5 - funding_z * 0.5)
        oi_norm = self._clamp01(1.0 + oi_change / 100.0)
        return 0.6 * funding_score + 0.4 * oi_norm

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
        stablecoin_market_cap = self._positive_number(token_data.get("macro_stablecoin_market_cap"))
        defi_market_cap = self._positive_number(token_data.get("macro_defi_market_cap"))
        if total_market_cap is None and btc_dominance is None and stablecoin_dominance is None and stablecoin_market_cap is None and defi_market_cap is None:
            return 0.0, 1.0
        cache_key = (total_market_cap, btc_dominance, stablecoin_dominance, stablecoin_market_cap, defi_market_cap)
        cached = self._macro_context_results.get(cache_key)
        if cached is not None:
            return cached

        score = 0.0
        observed = 0
        total_delta = self._macro_delta("TOTAL_MARKET_CAP", total_market_cap)
        btc_delta = self._macro_delta("BTC_DOMINANCE", btc_dominance)
        stable_delta = self._macro_delta("STABLECOIN_DOMINANCE", stablecoin_dominance)
        stable_slope = self._macro_delta("STABLECOIN_MARKET_CAP", stablecoin_market_cap)
        defi_delta = self._macro_delta("DEFI_MARKET_CAP", defi_market_cap)

        if total_delta is not None:
            observed += 1
            score += 0.25 if total_delta >= 0 else 0.0
        if btc_delta is not None:
            observed += 1
            score += 0.20 if btc_delta <= 0.25 else 0.0
        if stable_delta is not None:
            observed += 1
            score += 0.15 if stable_delta <= 0 else 0.0
        if stable_slope is not None:
            observed += 1
            score += 0.25 if stable_slope > 0 else 0.0
        if defi_delta is not None:
            observed += 1
            score += 0.15 if defi_delta >= 0 else 0.0
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

    def _log_factor_matrix(
        self,
        candidates: list[_CheapCandidate],
        scores_by_symbol: dict[str, float],
        momentum_scores: dict[str, float],
        bnb_reference: dict[str, Any] | None = None,
    ) -> None:
        """Append one JSONL row per evaluated symbol with the full factor matrix.

        Persists what the engine otherwise only emits as transient warnings:
        every factor boolean, the raw scoring inputs, the entry_score, and
        which inputs were missing. This is the join key for offline tuning of
        which factor combinations actually win. Gated and best-effort so it can
        never affect a live trading cycle.
        """

        if not getattr(self.settings, "factor_matrix_log_enabled", False):
            return
        path_str = getattr(self.settings, "factor_matrix_log_path", "logs/factor_matrix.jsonl")
        try:
            path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            # The regime decision reads BNB from the normalized reference, not
            # the token row, so judge "missing" from that same source — else the
            # log says BNB was missing even when the decision used it correctly.
            bnb_missing = (
                bnb_reference is None
                or self._bnb_change_1h_fraction(bnb_reference, separate_bnb_data=True) is None
            )
            lines = []
            for candidate in candidates:
                token_data = candidate.token_data
                row = {
                    "ts": now,
                    "symbol": candidate.symbol,
                    "entry_score": scores_by_symbol.get(candidate.symbol),
                    "momentum_z": momentum_scores.get(candidate.symbol, 0.0),
                    "factors": {
                        "volume_breakout": candidate.volume_breakout,
                        "six_hour_high_break": candidate.six_hour_high_break,
                        "regime_not_risk_off": candidate.regime_not_risk_off,
                        "rsi_in_range": candidate.rsi_in_range,
                        "derivatives_risk_clear": candidate.derivatives_risk_clear,
                    },
                    "inputs": {
                        "breakout_strength": candidate.breakout_strength,
                        "volume_surge_score": candidate.volume_surge_score,
                        "macro_score": candidate.macro_score,
                        "macro_size_multiplier": candidate.macro_size_multiplier,
                        "volume_24h": candidate.volume_24h,
                        "momentum_1h": candidate.momentum_1h,
                        "momentum_24h": candidate.momentum_24h,
                        "momentum_7d": candidate.momentum_7d,
                        "momentum_30d": candidate.momentum_30d,
                        "momentum_90d": candidate.momentum_90d,
                        "atr_ratio": candidate.atr_ratio,
                        "cmc_rank": candidate.cmc_rank,
                        "watchlist_count": candidate.watchlist_count,
                        "circulating_supply": candidate.circulating_supply,
                    },
                    "missing": {
                        "rsi": self._positive_number(token_data.get("rsi")) is None,
                        "funding_rate": self._number(token_data.get("funding_rate")) is None,
                        "open_interest_change_pct": self._number(
                            token_data.get("open_interest_change_pct")
                        )
                        is None,
                        "bnb_1h_trend": bnb_missing,
                    },
                }
                lines.append(json.dumps(row, default=str))
            if lines:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            LOGGER.debug("Could not write factor matrix log: %s", exc)

    def _warn_missing_factor_once(self, symbol: str, factor: str) -> None:
        key = (symbol.upper(), factor)
        if key in self._missing_factor_warnings:
            return
        self._missing_factor_warnings.add(key)
        LOGGER.warning("Missing data for %s factor on %s; failing factor closed", factor, symbol)

    def _bnb_reference(self, market_snapshot: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        """Return a normalized standalone BNB row for the regime check.

        The top-level ``BNB`` (or ``WBNB``) snapshot row exists purely as a
        regime reference and is skipped by the per-token scan, so without this
        it never reaches ``check_regime`` and the regime factor fails closed for
        every token (halving size via ``regime_size_multiplier``).

        The 1h move is canonicalized into percent points under
        ``bnb_1h_trend_pct``. The separate-BNB branch reads that alias first as
        percent points, which avoids the scaling bug where a percent-point
        value (e.g. 1.5 == 1.5%) would otherwise be misread as a raw fraction
        (1.5 == 150%).
        """

        row = market_snapshot.get("BNB") or market_snapshot.get("WBNB")
        if not isinstance(row, dict):
            return None
        normalized = dict(row)
        trend = None
        for key in ("bnb_1h_trend_pct", "percent_change_1h", "price_change_percentage_1h", "change_1h"):
            candidate = self._number(normalized.get(key))
            if candidate is not None:
                trend = candidate
                break
        if trend is not None:
            normalized["bnb_1h_trend_pct"] = trend
        return normalized

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
            # bnb_1h_trend_pct is canonicalized to percent points by
            # _bnb_reference and read FIRST, so a percent-point value (e.g. 1.5
            # == 1.5%) is never misread as a raw fraction (1.5 == 150%). The
            # raw percent_change_1h alias keeps its legacy fraction semantics as
            # a last resort, but the real engine path never reaches it because
            # _bnb_reference always populates bnb_1h_trend_pct.
            return self._first_change_fraction(
                data,
                (
                    ("bnb_1h_trend_pct", "percent_points"),
                    ("bnb_percent_change_1h", "fraction"),
                    ("price_change_percentage_1h", "percent_points"),
                    ("change_1h", "percent_points"),
                    ("percent_change_1h", "fraction"),
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
