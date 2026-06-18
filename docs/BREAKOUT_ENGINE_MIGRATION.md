# Breakout Engine Migration Guide — v2.0

> **Baseline:** `agent/breakout-swarm-base` (commit `daf4af4`)  
> **Target branch:** `agent/agent5-docs`  
> **Scope:** Documentation-only; source changes are tracked in sibling branches.

---

## 1. Executive Summary

This release upgrades the breakout engine from its v1.0 baseline to a **tighter, regime-aware, precision-first** scoring system. It fixes four critical bugs that caused false entries and phantom score inflation, and adds five precision/recall improvements.

| Category | Count | Impact |
| --- | --- | --- |
| **P0 Bug Fixes** | 4 | Eliminate free score inflation, phantom derivatives, and risk-off entries |
| **P1 Improvements** | 5 | Sharper threshold, stronger consensus, macro-weighted scoring, A/B telemetry |

**Deployment cadence:** Shadow → 7-day bake → ramp 25% → 50% → 100%.

**Safe rollback:** Revert env vars to old values; `agent/breakout-swarm-base` is the clean rollback point.

---

## 2. P0 Bug Fixes

### Bug 1 — Derivatives factor passed when data was missing (`DERIVATIVES_NEUTRAL_ON_MISSING`)

- **Severity:** P0 — free score inflation on every token
- **Description:** CMC does not provide funding or open-interest data. When `derivatives_neutral_on_missing=true`, the engine treated missing data as a **neutral pass**, awarding a free `10.0` points to every candidate. This permanently capped the score at 5/6 for candidates with real data and inflated everyone else.
- **Fix:** Set `DERIVATIVES_NEUTRAL_ON_MISSING=false` so missing data **fails closed**. The derivatives factor contributes `0.0` until a real Binance futures feed is wired.
- **Code snippet (settings default):**

```python
# src/config/settings.py
derivatives_neutral_on_missing: bool = False  # was True
```

- **Code snippet (engine logic):**

```python
# src/strategy/6falgorithm/breakout_engine.py
if funding_rate is None or open_interest_change is None:
    if bool(getattr(self.settings, "derivatives_neutral_on_missing", False)):
        derivatives_risk_clear = True   # OLD — free pass
    else:
        self._warn_missing_factor_once(symbol, "derivatives_risk_clear")
        derivatives_risk_clear = False  # NEW — fail closed
```

---

### Bug 2 — Derivatives weight active with no real data (`BREAKOUT_SCORE_WEIGHT_DERIVATIVES`)

- **Severity:** P0 — scoring model trusts a data source that does not exist
- **Description:** Even with `derivatives_neutral_on_missing=false`, the weight table still allocated `10.0` points to the derivatives factor. Since CMC structurally lacks funding/OI, this weight became a dead zone that could never be earned, distorting the relative importance of all other factors.
- **Fix:** Zero the weight until a real Binance feed is integrated. The slot stays reserved in the weight table so re-enabling it is a one-line change.
- **Code snippet:**

```python
# src/config/settings.py
breakout_score_weight_derivatives: float = 0.0  # was 10.0
```

---

### Bug 3 — Entries allowed in risk-off regime (`BREAKOUT_BLOCK_IN_RISK_OFF_REGIME`)

- **Severity:** P0 — macro guard was advisory only
- **Description:** The regime detector computed `regime_not_risk_off`, but the engine only applied it as a **size multiplier** (`0.5×`). It never blocked entries outright. In a strong BNB downtrend or broad-market fear, the bot would still enter at half size — catching falling knives.
- **Fix:** Enable the hard block. The quality-guard layer now returns `should_enter=False` with reason `"rule-based regime is risk-off"` whenever the regime gate fails.
- **Code snippet:**

```python
# src/config/settings.py
breakout_block_in_risk_off_regime: bool = True  # was False
```

```python
# src/strategy/6falgorithm/breakout_engine.py
if block_risk_off and not candidate.regime_not_risk_off:
    return False, "rule-based regime is risk-off", quality_guards
```

---

### Bug 4 — RSI was not required as a gate (`BREAKOUT_REQUIRE_RSI_IN_RANGE`)

- **Severity:** P0 — momentum entry without momentum confirmation
- **Description:** RSI was evaluated and scored, but a missing or out-of-range RSI did **not** block entry. The engine could enter a breakout when the token was already overbought (>75) or oversold (<55), dramatically increasing reversal risk.
- **Fix:** Make RSI a mandatory gate. The quality-guard layer blocks entry when RSI is outside the 55–75 band or missing.
- **Code snippet:**

```python
# src/config/settings.py
breakout_require_rsi_in_range: bool = True  # was False
```

```python
# src/strategy/6falgorithm/breakout_engine.py
if require_rsi and not candidate.rsi_in_range:
    return False, "RSI missing or outside 55–75 band", quality_guards
```

---

## 3. P1 Improvements

### Improvement 1 — Raise entry score threshold (`BREAKOUT_ENTRY_SCORE_MIN`)

- **Change:** `45.0` → `50.0`
- **Impact:** Reduces false-positive entries by requiring a stronger composite signal. Expected ~15–20% reduction in marginal entries with minimal impact on true breakout capture.
- **Configuration:**

```python
breakout_entry_score_min: float = 50.0  # was 45.0
```

---

### Improvement 2 — Invert quote-floor buffer (`BREAKOUT_QUOTE_SCORE_BUFFER`)

- **Change:** `5.0` → `-3.0`
- **Impact:** The old floor was `threshold - 5` = `40`, meaning candidates scoring `40–44` were still sent for expensive TWAK quotes. The new floor is `threshold + 3` = `53`, so only candidates that already exceed the entry threshold by a margin get quoted. This reduces quote waste and slippage exposure on borderline setups.
- **Configuration:**

```python
breakout_quote_score_buffer: float = -3.0  # was 5.0
```

> **Note:** The engine's floor property must be updated to support negative buffers:
> ```python
> @property
> def _quote_score_floor(self) -> float:
>     threshold = float(getattr(self.settings, "breakout_entry_score_min", 45.0))
>     buffer = float(getattr(self.settings, "breakout_quote_score_buffer", 5.0))
>     return max(0.0, threshold + buffer)  # NEW: threshold + buffer, not threshold - buffer
> ```

---

### Improvement 3 — Increase macro weight (`BREAKOUT_SCORE_WEIGHT_MACRO`)

- **Change:** `5.0` → `15.0`
- **Impact:** Macro context (total market cap delta, BTC dominance, stablecoin dominance) now carries more voting power in the composite score. This improves precision in regime transitions and reduces entries into broad-market weakness.
- **Configuration:**

```python
breakout_score_weight_macro: float = 15.0  # was 5.0
```

> **Rebalancing:** The weight table must be re-normalized to sum to 100. See `BREAKOUT_ENGINE_SCORING.md` for the new regime-adjusted allocation.

---

### Improvement 4 — Enable factor-matrix logging (`FACTOR_MATRIX_LOG_ENABLED`)

- **Change:** `false` → `true`
- **Impact:** Every cycle writes one JSONL row per evaluated symbol with the full factor boolean matrix, raw inputs, entry score, and missing-field flags. This is the offline join key for A/B testing, shadow replay, and model training.
- **Configuration:**

```python
factor_matrix_log_enabled: bool = True  # was False
```

- **Disk impact:** On a 132-symbol universe, one row per symbol per 5-minute cycle ≈ ~38 MB/day uncompressed. Rotate via `LOG_ROTATE_MAX_MB`.

---

### Improvement 5 — Require stronger factor consensus (`BREAKOUT_MIN_TRUE_FACTOR_COUNT`)

- **Change:** `3` → `4`
- **Impact:** The quality-guard layer now requires at least 4 of 6 factors to pass before entry. This filters out setups where only volume and price are breaking out while momentum, RSI, derivatives, and macro are weak.
- **Configuration:**

```python
breakout_min_true_factor_count: int = 4  # was 3
```

---

### Improvement 6 — Expand TWAK quote slots (`MAX_UNIVERSE_TWAK_QUOTES`)

- **Change:** `2` → `4`
- **Impact:** More candidates can be quoted per cycle, reducing the chance that a high-scoring symbol is skipped because the quote budget was exhausted by earlier candidates. The ranking sort (entry score → breakout strength → momentum z → volume) ensures the best names still win the first slots.
- **Code change:**

```python
# src/strategy/6falgorithm/breakout_engine.py
MAX_UNIVERSE_TWAK_QUOTES = 4  # was 2
```

---

## 4. Configuration Migration Checklist

| Env Var | Old Value | New Value | Reason |
| --- | --- | --- | --- |
| `DERIVATIVES_NEUTRAL_ON_MISSING` | `true` | `false` | Stop free score inflation |
| `BREAKOUT_SCORE_WEIGHT_DERIVATIVES` | `10.0` | `0.0` (until real data) | Neutralize until Binance feed |
| `BREAKOUT_ENTRY_SCORE_MIN` | `45.0` | `50.0` | Reduce false positives |
| `BREAKOUT_QUOTE_SCORE_BUFFER` | `5.0` | `-3.0` | Floor = threshold + 3 |
| `BREAKOUT_SCORE_WEIGHT_MACRO` | `5.0` | `15.0` | Macro matters |
| `FACTOR_MATRIX_LOG_ENABLED` | `false` | `true` | Needed for A/B testing |
| `BREAKOUT_MIN_TRUE_FACTOR_COUNT` | `3` | `4` | Require stronger consensus |
| `BREAKOUT_REQUIRE_RSI_IN_RANGE` | `false` | `true` | RSI is a gate |
| `BREAKOUT_BLOCK_IN_RISK_OFF_REGIME` | `false` | `true` | Block in bad macro |
| `MAX_UNIVERSE_TWAK_QUOTES` | `2` | `4` | More quote slots |

### Pre-migration checklist

- [ ] Confirm Binance futures feed is **NOT** wired → `BREAKOUT_SCORE_WEIGHT_DERIVATIVES=0.0` is safe.
- [ ] Confirm disk has > 500 MB free for factor-matrix logs.
- [ ] Back up current `.env` to `.env.pre-v2.0`.
- [ ] Verify `agent/breakout-swarm-base` is reachable: `git checkout agent/breakout-swarm-base`.

### Post-migration verification

- [ ] Run `pytest` — all 273+ cases pass.
- [ ] Run paper-trade loop for 1 cycle; check `logs/factor_matrix.jsonl` for non-empty rows.
- [ ] Confirm no entry decisions with `true_factor_count < 4`.
- [ ] Confirm no entries when `regime_not_risk_off=false`.
- [ ] Confirm RSI missing → `should_enter=false`.

---

## 5. A/B Testing Protocol

### Phase 0 — Shadow mode (Day 0)

1. Deploy the new engine on a **shadow** branch with `PAPER_TRADE=true`.
2. The live loop continues on the old engine.
3. Shadow loop writes `decision_shadow.jsonl` with identical market data but v2.0 scoring.
4. Compare:
   - Entry count per day
   - Average entry score
   - Factor composition of entries (which 4+ factors are true)
   - Missing-factor rate

### Phase 1 — 7-day bake (Days 1–7)

- Run shadow in parallel with live.
- At the end of each day, compute **shadow PnL** using post-hoc price trajectories (hold for 8h or until trailing stop).
- Compare shadow PnL vs. live PnL.
- **Gate:** Shadow must show ≥ 0% drawdown and ≥ 10% fewer false-positive entries (proxy: entries with score 50–55 that would have been blocked under v1.0's 45 threshold).

### Phase 2 — Ramp 25% (Days 8–10)

- If Phase 1 passes, route 25% of trading cycles to v2.0.
- Keep 75% on v1.0 baseline.
- Monitor live PnL, drawdown, and entry count hourly.
- **Abort trigger:** Live PnL underperforms shadow by > 5% or drawdown exceeds 5% in 24h.

### Phase 3 — Ramp 50% (Days 11–13)

- If Phase 2 passes, split 50/50.
- Continue monitoring.

### Phase 4 — Full cutover (Day 14+)

- If Phase 3 passes, route 100% to v2.0.
- Keep `agent/breakout-swarm-base` tagged and ready for instant rollback.

---

## 6. Rollback Plan

### Immediate rollback (< 5 minutes)

1. Stop the agent loop.
2. Revert env vars to pre-v2.0 values (see `.env.pre-v2.0` backup).
3. Restart loop.

### Clean rollback (< 15 minutes)

1. `git checkout agent/breakout-swarm-base`
2. `cp .env.pre-v2.0 .env`
3. `pip install -r requirements.txt` (if dependencies changed)
4. Restart loop.

### Rollback triggers

- Live PnL underperforms v1.0 baseline by > 5% over 48h
- Drawdown exceeds 10% (soft-stop threshold) within 24h of ramp
- Factor-matrix log shows > 20% of entries with `missing.rsi=true` or `missing.bnb_1h_trend=true` (data quality collapse)
- TWAK quote failure rate rises above 15% (increase from 2 to 4 slots may overload the router)

---

## 7. Deployment Checklist

### Pre-deploy

- [ ] All P0 bug fixes are merged into `agent/agent5-docs` (or target branch).
- [ ] All P1 improvements are merged.
- [ ] `pytest` passes with 100% success rate.
- [ ] `.env` values are updated per the Configuration Migration Checklist.
- [ ] `.env.pre-v2.0` backup exists.
- [ ] `logs/` directory has > 500 MB free disk space.
- [ ] `agent/breakout-swarm-base` is confirmed reachable.
- [ ] Telegram/health-check alerts are configured for drawdown and kill-switch.
- [ ] Shadow mode loop has been running for ≥ 24h with no errors in `decision_shadow.jsonl`.

### Deploy

- [ ] Stop live loop.
- [ ] `git pull` target branch.
- [ ] Verify `src/strategy/6falgorithm/breakout_engine.py` contains the new floor logic (`threshold + buffer`).
- [ ] Verify `MAX_UNIVERSE_TWAK_QUOTES = 4` in the same file.
- [ ] Verify `src/config/settings.py` defaults match the new values.
- [ ] Start loop with `--live --preflight` first.
- [ ] If preflight passes, start live loop.
- [ ] Confirm first cycle writes to `logs/factor_matrix.jsonl`.
- [ ] Confirm first cycle decision is `WAIT` (no panic entry on restart).

### Post-deploy verification (first 4 hours)

- [ ] No `ERROR` or `CRITICAL` in logs.
- [ ] Entry count is within ±20% of historical average (if market conditions are similar).
- [ ] No entries with `entry_score < 50.0`.
- [ ] No entries with `true_factor_count < 4`.
- [ ] No entries in risk-off regime (`regime_not_risk_off=false`).
- [ ] TWAK quote success rate ≥ 85%.
- [ ] Factor-matrix log rows contain all expected keys (`factors`, `inputs`, `missing`).
- [ ] Disk usage is growing at expected rate (< 2 MB/hour for 132-symbol universe).

### Post-deploy verification (24 hours)

- [ ] Compare daily entry count vs. v1.0 baseline.
- [ ] Compare average entry score vs. v1.0 baseline (should be higher).
- [ ] Verify no RSI-missing entries were allowed.
- [ ] Verify shadow PnL (if still running) aligns with live PnL within ±3%.
- [ ] Review `risk_events.jsonl` for any new guardrail triggers.
- [ ] Confirm `.env` values were not accidentally reverted by a restart or CI/CD overwrite.

---

*End of migration guide. For scoring details, see `BREAKOUT_ENGINE_SCORING.md`. For env var reference, see `ENV_TEMPLATE.md`.*
