# Audit handoff — Rule-based sizing, volatility-aware targets, and forced realization

**Repo:** `cascade-ai` (BNB Hack competition trading bot)
**Branch:** `feat/rule-based-sizing-targets-exits` (off `main`)
**Date:** 2026-06-14
**Author of changes:** Claude (Cowork session)
**Purpose of this doc:** complete, self-contained record so a second model (Kimi) can audit the diagnosis and the code changes in a fresh session with no prior context.

---

## 0. TL;DR for the auditor

The live bot opened 13 positions and sold **zero**. Two root causes:

1. **Every position got a flat +15% take-profit and a 6% trailing stop**, regardless of the asset's volatility. On a low-vol large cap (ETH) a +15% move in the ~7-day window is ~2.3 sigma (≈1% probability), while the 6% stop is ~1 sigma — structurally negative expectancy ("miracle" target). The volatility-aware function that would fix this (`calculate_exit_levels`) **existed but was never called** on the live entry path.
2. **Breakout mode has no time-based or deadline-based exit.** Positions only close on stop-or-target. With an unreachable target and an un-breached stop, they sit forever, so the book never realizes and would arrive at the deadline as paper.

Three coupled fixes were implemented (all behind backward-compatible defaults):
- **Targets:** entry now derives stop/target from `calculate_exit_levels(entry, atr_pct, regime)` when market context is supplied. ETH-style cold-ATR entries get an 8% target (regime fallback), never the flat +15%.
- **Sizing:** `calculate_position_pct` stays risk-based on cold ATR by assuming a stop distance, instead of deploying a flat max position.
- **Realization:** a `max_hold_hours` time-stop forces turnover, and a `competition_end_utc` window-flatten liquidates the whole book to USDC shortly before the deadline.

**What to audit:** correctness of the exit-level math, the backward-compat branch in `open_position`, the time-stop precedence, the window-flatten time arithmetic, and whether the default values keep current behavior unchanged until the operator opts in via `.env`.

---

## 1. System context (verified, do not re-derive)

- The bot trades spot on BNB chain via a TWAK/PancakeSwap router. Strategy mode is `breakout` in the live `.env` (a separate `scalping` mode also exists).
- A dashboard renders `positions.json`. The "Risk corridor" line the operator sees (e.g. `-5.8% stop / +15.0% target`) is computed from the persisted entry/stop/target.
- Competition: code freeze ~June 20, trading window ~June 22–28. Internal drawdown kill switch at 18%; DQ near 30%.
- Wallet is small (~$4 Base USDC during testing), so live positions show as dust ($0.10–$0.50). Several $0.50 rows are **forced daily compliance swaps** (`COMPLIANCE_TRADE_USDC = 0.5`) to satisfy a 1-trade/day minimum — not conviction entries. SHIB/BONK rows show `$0` (price-feed failure), so those are effectively unmanaged.

### Evidence base for the diagnosis
- `.env.competition`: `TAKE_PROFIT_PCT=0.15`, `TRAILING_STOP_PCT=0.06`. Comment claims `calculate_exit_levels still ATR/regime-scales these at entry time` — **this comment is false** (see below).
- `src/main.py` (pre-change) entry path created positions via `PositionManager.open_position(...)`, which hardcoded `take_profit_price = entry * (1 + settings.take_profit_pct)` and `trailing_stop_price = entry * (1 - settings.trailing_stop_pct)`.
- `src/strategy/position_manager.py::calculate_exit_levels` was defined but **had zero call sites** in `src/` (verified by grep). Dead code.
- `decision_live.jsonl`: `"atr_pct": null` on every row → even where ATR scaling could apply, ATR was never available, so any ATR-derived path would hit its cold-start fallback.
- The flat target reproduces the dashboard exactly: all 10 managed positions show target `+15.0%`. The varying stops (`-1.5%` to `-6.0%`) are the same 6% trailing stop measured from each coin's high-water mark, expressed relative to entry. Verified: FIL high `0.7971 × 0.94 = 0.7493` = its shown stop, to the cent.

### ETH expectancy math (the "miracle" claim)
- ETH entry `1683.08`, target `1935.54` (+15.00%), stop `1586.16` (−5.76% from entry; −6.00% from high).
- Reward:risk ≈ 2.60:1 → breakeven hit-rate needed ≈ 27.7%.
- ETH ~daily vol 2–3% → ~7-day sigma 5.3–7.9% → +15% is **1.9–2.8 sigma** one-directional (≈1–3% probability). The 6% stop is ~1 sigma. So the target is almost never reached before the stop → negative expectancy. The same +15/−6 corridor is fine for a ~25%-weekly-vol microcap (DOGE/SHIB/BONK) where +15% is < 1 sigma — which is exactly why a **flat** target is wrong and a **volatility-scaled** one is right.

---

## 2. Changes made

Four files. Full diff stat:
```
 src/config/settings.py           |  10 ++
 src/main.py                      |  47 ++++++++-
 src/strategy/position_manager.py |  79 +++++++++++---
 tests/test_rule_based_exits.py   | 212 +++++++++++++++++++++++++++++++++++++++
```

All code below is the **actual post-change source** (verbatim), with the relevant
**pre-change** fragments shown for comparison. The auditor has no repo access, so
everything needed to verify the changes is inline here.

---

### 2.1 `src/strategy/position_manager.py`

#### Reference: `calculate_exit_levels` (UNCHANGED — the function that was dead code)
This already existed and was correct; the bug was that **nothing called it on the
live path**. It is now called from `open_position`. Shown so the auditor can verify
the target math:

```python
def calculate_exit_levels(
    entry_price: float,
    atr_pct: float | None,
    regime: object,
) -> tuple[float, float]:
    """Return trailing-stop and take-profit percentages for a regime."""

    regime_value = getattr(regime, "value", str(regime))
    if regime_value == "risk_off":
        return 0.025, 0.05
    if atr_pct is None or atr_pct <= 0:
        return 0.035, 0.08

    trailing_stop_pct = max(0.035, min(0.10, float(atr_pct) * 1.5))
    take_profit_pct = max(0.08, min(0.20, float(atr_pct) * 3.0))
    if regime_value == "trending_up":
        return trailing_stop_pct, take_profit_pct
    return min(trailing_stop_pct, 0.06), min(take_profit_pct, 0.12)
```

Note: for `atr_pct=None` (the production reality) and a non-risk-off regime this
returns `(0.035, 0.08)` → **8% target**, not the flat +15%. For `trending_up`
the target is `3×ATR` (clamped 8–20%); for other non-risk-off regimes it is
capped at 12%. The trailing stop is `1.5×ATR` (clamped). This is the whole point:
the target scales with the asset's volatility.

#### (a) `open_position` — volatility-aware exit levels, backward compatible

**BEFORE (pre-change):**
```python
def open_position(
    self,
    symbol: str,
    amount_tokens: float,
    entry_price: float,
    entry_value_usdc: float,
) -> Position:
    """Open and store a new position."""

    normalized = symbol.upper()
    assert_tradable_symbol(normalized)
    if normalized in self._positions:
        raise ValueError(f"{normalized} position is already open")
    now = datetime.now(timezone.utc)
    position = Position(
        symbol=normalized,
        amount_tokens=amount_tokens,
        entry_price=entry_price,
        entry_value_usdc=entry_value_usdc,
        highest_price=entry_price,
        trailing_stop_price=entry_price * (1 - self.settings.trailing_stop_pct),  # flat 6%
        take_profit_price=entry_price * (1 + self.settings.take_profit_pct),      # flat +15%
        opened_at=now,
        current_price=entry_price,
        current_price_at=now,
    )
    self._positions[normalized] = position
    self.persist_positions()
    return position
```

**AFTER (current source, verbatim):**
```python
def open_position(
    self,
    symbol: str,
    amount_tokens: float,
    entry_price: float,
    position_usd: float | None = None,
    atr_pct: float | None = None,
    regime: object | None = None,
    entry_value_usdc: float | None = None,
) -> Position:
    """Open and store a new position.

    When the caller supplies market context (``regime`` and/or ``atr_pct``)
    the exit levels become volatility-aware via ``calculate_exit_levels``,
    so a low-volatility large cap gets a reachable target instead of the
    flat ``take_profit_pct`` (the source of the +15% large-cap miracle
    targets). Legacy callers that pass neither keep the flat settings-based
    levels for backward compatibility.

    ``position_usd`` is the entry notional; ``entry_value_usdc`` is accepted
    as a backward-compatible alias.
    """

    normalized = symbol.upper()
    assert_tradable_symbol(normalized)
    if normalized in self._positions:
        raise ValueError(f"{normalized} position is already open")
    notional = position_usd if position_usd is not None else entry_value_usdc
    if notional is None:
        notional = 0.0
    now = datetime.now(timezone.utc)
    if regime is not None or atr_pct is not None:
        trailing_stop_pct, take_profit_pct = calculate_exit_levels(
            entry_price, atr_pct, regime
        )
    else:
        trailing_stop_pct = self.settings.trailing_stop_pct
        take_profit_pct = self.settings.take_profit_pct
    position = Position(
        symbol=normalized,
        amount_tokens=amount_tokens,
        entry_price=entry_price,
        entry_value_usdc=notional,
        highest_price=entry_price,
        trailing_stop_price=entry_price * (1 - trailing_stop_pct),
        take_profit_price=entry_price * (1 + take_profit_pct),
        opened_at=now,
        current_price=entry_price,
        current_price_at=now,
    )
    self._positions[normalized] = position
    self.persist_positions()
    return position
```

**Why this was the missing half of an already-intended wiring.** The live entry
path calls `open_position` through this wrapper, which *already* passed
`atr_pct`/`regime` and silently swallowed a `TypeError` to fall back to the flat
4-arg form. Because the **old signature did not accept those kwargs, the
`except TypeError` branch fired on every entry** — the bot never got
volatility-aware levels. The new signature accepts them, so the `try` branch now
succeeds. Wrapper (current source, verbatim):

```python
def _open_local_position_v25(
    position_manager: PositionManager,
    symbol: str,
    amount_tokens: float,
    entry_price: float,
    position_usd: float,
    atr_pct: float | None,
    regime: MarketRegime,
) -> None:
    try:
        position_manager.open_position(
            symbol=symbol,
            amount_tokens=amount_tokens,
            entry_price=entry_price,
            position_usd=position_usd,
            atr_pct=atr_pct,
            regime=regime,
        )
    except TypeError:
        position_manager.open_position(symbol, amount_tokens, entry_price, position_usd)
```

> **Audit point:** the `except TypeError` fallback is now effectively dead (the
> `try` always succeeds). It is retained as defensive back-compat. Confirm it can
> never mask a real `TypeError` raised *inside* `open_position` — it can, in
> principle, so a stricter version would catch a narrower signature error. Low
> risk because `open_position`'s body raises `ValueError`, not `TypeError`.

#### (b) `update_price` — time-stop added

**AFTER (current source, verbatim).** Only the last block before `return None`
and the new helper are new; the trailing-stop / reconstructed logic is unchanged:

```python
def update_price(self, symbol: str, current_price: float) -> str | None:
    """Update trailing stop state and return an exit reason when triggered."""

    normalized = symbol.upper()
    position = self._positions.get(normalized)
    if position is None:
        return None
    position.current_price = current_price
    position.current_price_at = datetime.now(timezone.utc)
    if position.reconstructed:
        # First live price for a reconstructed row: re-anchor stops from
        # the observed price ... then defer exit evaluation one cycle.
        position.highest_price = max(position.highest_price, current_price)
        position.trailing_stop_price = current_price * (1 - self.settings.trailing_stop_pct)
        if position.take_profit_price <= 0:
            position.take_profit_price = current_price * (1 + self.settings.take_profit_pct)
        position.reconstructed = False
        self.persist_positions()
        return None
    needs_persist = True
    if current_price > position.highest_price:
        position.highest_price = current_price
        raised_stop = current_price * (1 - self._active_trailing_stop_pct(position))
        position.trailing_stop_price = max(position.trailing_stop_price, raised_stop)
        self.persist_positions()
        needs_persist = False
    if needs_persist:
        self.persist_positions()
    if current_price >= position.take_profit_price:
        return "take_profit"
    if current_price <= position.trailing_stop_price:
        return "trailing_stop"
    if self._time_stop_triggered(position):   # <-- NEW: clock is checked LAST
        return "time_stop"
    return None

def _time_stop_triggered(self, position: Position) -> bool:
    """Force an exit when a position has been held past max_hold_hours.

    Breakout positions otherwise sit indefinitely whenever neither the
    target nor the trailing stop is hit. A max-hold clock guarantees
    turnover so capital is recycled and the book does not arrive at the
    competition deadline full of stale, never-realized positions.
    Disabled when ``max_hold_hours`` is 0 or unset.
    """

    max_hold_hours = float(getattr(self.settings, "max_hold_hours", 0.0) or 0.0)
    if max_hold_hours <= 0 or position.opened_at is None:
        return False
    opened_at = position.opened_at
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600.0
    return age_hours >= max_hold_hours
```

> **Audit point — precedence:** take-profit and trailing-stop are returned before
> the time-stop, so a winner is never closed by the clock. The exit reason
> `"time_stop"` flows through the existing `_execute_position_exit` (full
> market-sell to USDC) exactly like `"trailing_stop"`; no new execution path.

#### (c) `calculate_position_pct` — risk-based cold start

**BEFORE (pre-change), cold branch:**
```python
if atr_pct is None or atr_pct <= 0:
    # Deploy at the configured max_position_pct ... (FLAT MAX)
    fallback = max_position_pct * regime_mult * risk_mult
    return max(0.0, min(fallback, max_position_pct))
stop_distance_pct = max(0.015, min(0.08, float(atr_pct) * 2.0))
...
```

**AFTER (current source, verbatim):**
```python
def calculate_position_pct(
    equity_usd: float,
    atr_pct: float | None,
    regime_multiplier: float,
    risk_state_multiplier: float,
    loss_streak: int,
    max_position_pct: float = 0.05,
    base_risk_per_trade_pct: float = 0.0035,
    fallback_stop_pct: float = 0.06,
) -> float:
    """Calculate volatility-scaled position size as a decimal percentage.

    Sizing stays risk-based: position % = risk-budget / stop-distance, capped
    at ``max_position_pct``. When ATR is cold we substitute ``fallback_stop_pct``
    as the assumed stop distance rather than deploying a flat max position, so
    the rule (smaller size for wider stops) holds even before the price cache
    warms up.
    """

    if equity_usd <= 0 or max_position_pct <= 0:
        return 0.0
    regime_mult = max(0.0, float(regime_multiplier))
    risk_mult = max(0.0, float(risk_state_multiplier))
    if atr_pct is None or atr_pct <= 0:
        # Cold start: the price cache is not yet warm enough to compute ATR.
        # Size off an assumed stop distance so the position is still
        # risk-budgeted (not a flat max bet). Drawdown/daily-loss guardrails
        # still gate entries downstream.
        stop_distance_pct = max(0.015, min(0.10, float(fallback_stop_pct)))
    else:
        stop_distance_pct = max(0.015, min(0.08, float(atr_pct) * 2.0))
    raw_position_pct = base_risk_per_trade_pct / stop_distance_pct
    position_pct = min(max_position_pct, raw_position_pct)
    if loss_streak >= 2:
        position_pct *= 0.5
    position_pct *= regime_mult * risk_mult
    return max(0.0, min(position_pct, max_position_pct))
```

> **Audit point:** with the production defaults `base_risk=0.0035`, cold stop
> `0.06` → `raw = 0.0583 > max 0.05` → capped at `0.05`, identical to the old flat
> behavior, so the existing regression `test_missing_atr_uses_max_position_pct_cold_start`
> still passes. The change only bites when the operator sets a smaller risk budget
> (e.g. `0.008`), where the size correctly drops below the cap. So this is
> rule-based *and* non-breaking.

---

### 2.2 `src/config/settings.py`

**Fields added (verbatim):**
```python
trailing_stop_pct: float = 0.06
take_profit_pct: float = 0.08
base_risk_per_trade_pct: float = 0.0035
# Realization rules (breakout mode). max_hold_hours forces a time-stop on
# positions that never hit target or trailing stop (0 disables). The
# competition window flatten liquidates the whole book to USDC shortly
# before the deadline so the final score is realized cash, not paper.
max_hold_hours: float = 0.0
competition_end_utc: str = ""
flatten_before_end_minutes: int = 30
risk_off_max_slippage_pct: float = 0.005
```

**Env mappings added (verbatim):**
```python
"base_risk_per_trade_pct": _get_float("BASE_RISK_PER_TRADE_PCT", 0.0035),
"max_hold_hours": _get_float("MAX_HOLD_HOURS", 0.0),
"competition_end_utc": os.getenv("COMPETITION_END_UTC", ""),
"flatten_before_end_minutes": _get_int("FLATTEN_BEFORE_END_MINUTES", 30),
"risk_off_max_slippage_pct": _get_float("RISK_OFF_MAX_SLIPPAGE_PCT", 0.005),
```
All three default to a no-op (`0.0` / `""` / unused).

---

### 2.3 `src/main.py`

**Import change:** `from datetime import datetime, timezone` → now
`from datetime import datetime, timedelta, timezone` (added `timedelta`).

**New helper (current source, verbatim):**
```python
def _maybe_flatten_for_window(
    settings: Settings,
    position_manager: PositionManager,
    router: PancakeSwapRouter,
    guardrails: Guardrails,
    now: datetime,
) -> bool:
    """Liquidate the whole book to USDC shortly before the competition deadline.

    Returns True when the flatten window is active (caller should also block new
    entries for the rest of the run). No-op when ``competition_end_utc`` is unset
    or unparseable, so default behaviour is unchanged.
    """

    end_iso = (getattr(settings, "competition_end_utc", "") or "").strip()
    if not end_iso:
        return False
    try:
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.warning("Invalid COMPETITION_END_UTC=%r; window flatten disabled", end_iso)
        return False
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    flatten_minutes = float(getattr(settings, "flatten_before_end_minutes", 30) or 0.0)
    if now < end_dt - timedelta(minutes=flatten_minutes):
        return False
    open_positions = position_manager.list_open_positions()
    if open_positions:
        LOGGER.warning(
            "Competition window flatten: liquidating %s open positions before deadline %s",
            len(open_positions),
            end_dt.isoformat(),
        )
        emergency_liquidate(position_manager, router, guardrails)
    return True
```

**Loop wiring (current source, verbatim).** Right after the price cache update:
```python
_update_price_cache(price_cache, market_snapshot, now_utc)
window_flatten_active = _maybe_flatten_for_window(
    settings, position_manager, router, guardrails, now_utc
)
```
And where entries are gated:
```python
entries_allowed = _risk_allows_new_entries(guardrails, risk_decision, portfolio_value, settings)
if window_flatten_active:
    entries_allowed = False
entries_blocked_reason = None if entries_allowed else _entries_blocked_reason(
    guardrails,
    risk_decision,
    portfolio_value,
    settings,
)
if window_flatten_active:
    entries_blocked_reason = "competition_window_flatten"
```

`emergency_liquidate` is reused (not rewritten). For the auditor, its body
(UNCHANGED) market-sells every open position to the stable symbol:
```python
def emergency_liquidate(position_manager, router, guardrails) -> None:
    stable_symbol = guardrails.settings.default_stable_symbol
    for position in position_manager.list_open_positions():
        if position.symbol == stable_symbol:
            continue
        LOGGER.warning("Emergency liquidating %s", position.symbol)
        execution_slippage = _require_execution_slippage(guardrails.settings.max_slippage_pct)
        result = _execute_logged_swap(
            guardrails.settings, router, "emergency_liquidation",
            position.symbol, stable_symbol, position.amount_tokens, execution_slippage,
        )
        if not _execution_has_tx_hash(result):
            LOGGER.error("Emergency liquidation for %s returned no tx hash; ...", position.symbol)
            continue
        position_manager.close_position(position.symbol)
```

> **Audit point — window math:** `flatten_at = end_dt - flatten_before_end_minutes`.
> The function fires for *all* `now >= flatten_at` (including after the deadline),
> so a late/restarted process still flattens. Naive ISO strings are coerced to
> UTC. An empty or unparseable `COMPETITION_END_UTC` is a hard no-op. Confirm the
> loop calls this **every cycle**, so once inside the window it re-flattens any
> residual/reconstructed positions and keeps entries blocked.

---

## 3. Tests

New file `tests/test_rule_based_exits.py` (10 tests). Results in this sandbox (Linux, py3.10):

- **8/8 pure-logic tests PASS** (exit levels, sizing fallback, time-stop precedence):
  - `test_open_position_with_regime_uses_volatility_aware_levels` — ETH cold-ATR target = 8%, `< 0.15`.
  - `test_open_position_target_scales_with_atr` — higher ATR ⇒ wider target.
  - `test_open_position_legacy_callers_keep_flat_levels` — no-context call still yields the flat settings target (120.0 / 94.0). Guards backward compat.
  - `test_cold_atr_sizing_is_risk_based_not_flat`, `test_cold_atr_wider_assumed_stop_means_smaller_size`.
  - `test_time_stop_fires_after_max_hold`, `test_time_stop_disabled_by_default`, `test_target_still_wins_over_time_stop`.
- **9/9 existing `tests/test_position_manager_v2.py` PASS** (no regression).
- All edited files pass `python -m py_compile`.
- **2 window-flatten tests NOT RUN in sandbox** (`test_window_flatten_helper`, `test_window_flatten_disabled_when_unset`): they import `src.main`, which pulls `bnb-chain-agentkit` / `httpx` / `web3` (and `bnb-chain-agentkit` requires py3.12). **Action for auditor / operator:** run the full suite on the py3.12 env (EC2 or local venv):
  ```bash
  python -m pytest tests/test_rule_based_exits.py tests/test_position_manager_v2.py -q
  ```
  Expected ~330 passed for the whole suite (2 pre-existing ML failures are known sandbox issues per project notes).

### 3.1 Full test file (`tests/test_rule_based_exits.py`, verbatim)

The auditor should read these to confirm the assertions match the intended
behavior. Note the two window tests do their `src.main` import *inside* the test
body, so they do not break collection in dependency-light environments.

```python
"""Tests for rule-based sizing, volatility-aware targets, and realization rules.

Covers the three coupled changes:
  1. open_position derives exit levels from ATR/regime when context is supplied
     (no more flat +15% target on low-vol large caps), and keeps flat
     settings-based levels for legacy callers.
  2. calculate_position_pct stays risk-based on cold ATR (assumed stop distance)
     instead of deploying a flat max position.
  3. A max-hold time-stop forces turnover; the competition-window flatten
     liquidates the book before the deadline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config.settings import Settings
from src.strategy.position_manager import (
    PositionManager,
    calculate_exit_levels,
    calculate_position_pct,
)
from src.strategy.regime_detector import MarketRegime


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = dict(position_state_path=str(tmp_path / "positions.json"))
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- 1. Volatility-aware targets ------------------------------------------


def test_open_position_with_regime_uses_volatility_aware_levels(tmp_path: Path) -> None:
    # ETH-style: ATR cold, but regime context supplied -> target must NOT be
    # the flat +15%. calculate_exit_levels falls back to 8% TP, not 15%.
    settings = _settings(tmp_path, take_profit_pct=0.15, trailing_stop_pct=0.06)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "ETH",
        amount_tokens=1.0,
        entry_price=1683.08,
        position_usd=100.0,
        atr_pct=None,
        regime=MarketRegime.TRENDING_UP,
    )
    target_pct = pos.take_profit_price / pos.entry_price - 1.0
    assert target_pct < 0.15  # the miracle +15% is gone
    assert round(target_pct, 4) == 0.08


def test_open_position_target_scales_with_atr(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = PositionManager(settings)
    low_vol = manager.open_position(
        "ETH", amount_tokens=1.0, entry_price=1000.0, position_usd=100.0,
        atr_pct=0.02, regime=MarketRegime.TRENDING_UP,
    )
    manager.close_position("ETH")
    high_vol = manager.open_position(
        "DOGE", amount_tokens=1.0, entry_price=1.0, position_usd=100.0,
        atr_pct=0.06, regime=MarketRegime.TRENDING_UP,
    )
    low_target = low_vol.take_profit_price / low_vol.entry_price - 1.0
    high_target = high_vol.take_profit_price / high_vol.entry_price - 1.0
    # A volatile microcap earns a wider target than a calm large cap.
    assert high_target > low_target


def test_open_position_legacy_callers_keep_flat_levels(tmp_path: Path) -> None:
    settings = _settings(tmp_path, take_profit_pct=0.20, trailing_stop_pct=0.06)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "CAKE", amount_tokens=1.0, entry_price=100.0, entry_value_usdc=100.0,
    )
    assert round(pos.take_profit_price, 2) == 120.0
    assert round(pos.trailing_stop_price, 2) == 94.0


# --- 2. Risk-based sizing on cold ATR -------------------------------------


def test_cold_atr_sizing_is_risk_based_not_flat() -> None:
    # With a risk budget whose risk-based size sits below the cap, the
    # cold-start size must equal that risk-based value (budget / assumed stop)
    # -- not the flat max position.
    max_pct = 0.20
    size = calculate_position_pct(
        1000, None, 1.0, 1.0, 0,
        max_position_pct=max_pct, base_risk_per_trade_pct=0.008, fallback_stop_pct=0.06,
    )
    assert round(size, 4) == round(0.008 / 0.06, 4)
    assert size < max_pct  # proves it is risk-based, not pinned to the cap


def test_cold_atr_wider_assumed_stop_means_smaller_size() -> None:
    tight = calculate_position_pct(
        1000, None, 1.0, 1.0, 0, max_position_pct=0.50,
        base_risk_per_trade_pct=0.02, fallback_stop_pct=0.04,
    )
    wide = calculate_position_pct(
        1000, None, 1.0, 1.0, 0, max_position_pct=0.50,
        base_risk_per_trade_pct=0.02, fallback_stop_pct=0.10,
    )
    assert wide < tight


# --- 3a. Time-stop ---------------------------------------------------------


def test_time_stop_fires_after_max_hold(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_hold_hours=12.0, take_profit_pct=0.20)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    # Backdate the open so the position is stale.
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=13)
    # Price sits between stop and target -> only the time-stop can fire.
    reason = manager.update_price("DOT", 1.0)
    assert reason == "time_stop"


def test_time_stop_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)  # max_hold_hours defaults to 0
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=100)
    assert manager.update_price("DOT", 1.0) is None


def test_target_still_wins_over_time_stop(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_hold_hours=1.0)
    manager = PositionManager(settings)
    pos = manager.open_position(
        "DOT", amount_tokens=1.0, entry_price=1.0, position_usd=10.0,
        atr_pct=0.03, regime=MarketRegime.TRENDING_UP,
    )
    pos.opened_at = datetime.now(timezone.utc) - timedelta(hours=99)
    # Price above target -> take_profit takes precedence over time_stop.
    assert manager.update_price("DOT", 5.0) == "take_profit"


# --- 3b. Competition-window flatten ---------------------------------------


def test_window_flatten_helper(tmp_path: Path) -> None:
    from src import main as main_mod

    class _Router:
        pass

    class _FakeManager:
        def __init__(self) -> None:
            self._open = ["ETH", "DOGE"]

        def list_open_positions(self):
            return list(self._open)

    class _Guardrails:
        pass

    captured = {}

    def _fake_liquidate(pm, router, guardrails):  # noqa: ANN001
        captured["called"] = True
        pm._open.clear()

    orig = main_mod.emergency_liquidate
    main_mod.emergency_liquidate = _fake_liquidate
    try:
        end = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
        settings = _settings(
            tmp_path,
            competition_end_utc=end.isoformat(),
            flatten_before_end_minutes=30,
        )
        pm = _FakeManager()
        # Well before the window -> no flatten.
        assert main_mod._maybe_flatten_for_window(
            settings, pm, _Router(), _Guardrails(), end - timedelta(hours=2)
        ) is False
        assert "called" not in captured
        # Inside the flatten window -> flatten fires and entries are blocked.
        assert main_mod._maybe_flatten_for_window(
            settings, pm, _Router(), _Guardrails(), end - timedelta(minutes=10)
        ) is True
        assert captured.get("called") is True
    finally:
        main_mod.emergency_liquidate = orig


def test_window_flatten_disabled_when_unset(tmp_path: Path) -> None:
    from src import main as main_mod

    settings = _settings(tmp_path, competition_end_utc="")

    class _PM:
        def list_open_positions(self):
            return []

    assert main_mod._maybe_flatten_for_window(
        settings, _PM(), object(), object(), datetime.now(timezone.utc)
    ) is False
```

---

## 4. Activation — operator must set these in `.env` (changes are inert until then)

```bash
# Volatility-aware targets are automatic on the live entry path (regime is always
# passed). To also enable forced realization:
MAX_HOLD_HOURS=18                       # time-stop; tune 12-24h for a 1-week window
COMPETITION_END_UTC=2026-06-28T23:59:00Z  # set the real deadline
FLATTEN_BEFORE_END_MINUTES=30           # liquidate 30 min before close
```
Optional, to make sizing meaningfully risk-based (currently `BASE_RISK_PER_TRADE_PCT=0.0035`, `MAX_POSITION_PCT=0.05` are conservative; `.env.competition` intended 20% conviction / 2% risk):
```bash
BASE_RISK_PER_TRADE_PCT=0.02
MAX_POSITION_PCT=0.20
```

Deploy is manual (scp `src` + `tests` → restart `cascade-ai.service`); the operator does this, Claude has no SSH.

---

## 5. Deliberately NOT done (flag for follow-up / audit scope)

- **Partial profit-taking / scale-out at +1R** was discussed but not implemented. It requires partial-fill execution (sell a fraction, decrement `amount_tokens`) which touches the live swap path and could not be integration-tested here. The time-stop + window-flatten already guarantee realization; scale-out is the next increment to bank PnL earlier.
- **Compliance dust swaps** (`COMPLIANCE_TRADE_USDC = 0.5`) were left in place. They still create junk $0.50 positions; consider gating them off once real rule-based entries flow.
- **ATR is null in production** (`price_cache.get_atr_pct` returns None). The changes degrade gracefully to the regime fallback (8% target), but warming ATR would make both sizing and targets fully volatility-scaled. The data layer was declared DONE/frozen by the project; investigate `get_atr_pct` cache warmth separately.
- **Schema:** no `positions.json` / decision-row schema fields were renamed; the dashboard's Zod parser ignores unknown fields. `entry_value_usdc` is preserved as a field and as a kwarg alias.

## 6. Suggested audit checklist for Kimi

1. Re-derive the ETH expectancy figures (reward:risk, sigma) and confirm the "flat target is wrong" conclusion.
2. Confirm `calculate_exit_levels` had no call sites pre-change and now exactly one (via `open_position`); confirm the live entry passes `regime`.
3. Verify the `open_position` backward-compat branch: legacy `entry_value_usdc=` callers and 4-positional callers keep flat behavior; context callers get scaled levels.
4. Verify time-stop precedence (target/stop beat the clock) and that `max_hold_hours=0` fully disables it.
5. Verify `_maybe_flatten_for_window` time math (naive-UTC handling, the `end - flatten_minutes` boundary) and that an empty/invalid `competition_end_utc` is a safe no-op.
6. Run the full py3.12 suite and confirm the 2 window tests pass and no regressions.
7. Sanity-check that defaults leave production behavior unchanged until `.env` opts in.
