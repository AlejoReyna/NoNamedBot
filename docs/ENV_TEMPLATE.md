# Environment Variable Template — Breakout Engine v2.0

> **Source of truth:** `src/config/settings.py`  
> **File to copy:** `.env.example` → `.env` (never commit `.env`)  
> **Applies to:** Breakout strategy mode (`STRATEGY_MODE=breakout`)

This document lists **all new or changed** environment variables for the v2.0 migration. Variables not listed here keep their v1.0 defaults.

---

## P0 Bug Fixes

### `DERIVATIVES_NEUTRAL_ON_MISSING`
- **Description:** When funding or open-interest data is missing (CMC does not provide it), treat the factor as neutral/pass instead of failing closed.
- **Old default:** `true` (free score inflation)
- **New default:** `false` (fail closed)
- **Example:**
  ```env
  DERIVATIVES_NEUTRAL_ON_MISSING=false
  ```
- **Belongs to:** P0 Bug 1 — Stop free score inflation on missing derivatives data.
- **Reversible:** Set back to `true` once a real Binance futures feed is wired.

### `BREAKOUT_SCORE_WEIGHT_DERIVATIVES`
- **Description:** Weight allocated to the derivatives factor in the composite entry score.
- **Old default:** `10.0`
- **New default:** `0.0` (neutralized until real data feed)
- **Example:**
  ```env
  BREAKOUT_SCORE_WEIGHT_DERIVATIVES=0.0
  ```
- **Belongs to:** P0 Bug 2 — Scoring model trusted a data source that does not exist.
- **Reversible:** Restore to `10.0` (or re-normalize) once Binance funding/OI data is available.

### `BREAKOUT_REQUIRE_RSI_IN_RANGE`
- **Description:** Require RSI to be inside the 55–75 band as a mandatory quality guard before entry.
- **Old default:** `false` (RSI was scored but not gating)
- **New default:** `true` (RSI is a gate)
- **Example:**
  ```env
  BREAKOUT_REQUIRE_RSI_IN_RANGE=true
  ```
- **Belongs to:** P0 Bug 4 — Prevent entries into overbought/oversold reversals.

### `BREAKOUT_BLOCK_IN_RISK_OFF_REGIME`
- **Description:** Block all entries when the regime detector classifies the market as risk-off (BNB weak, token downtrending).
- **Old default:** `false` (regime only reduced size by 50%)
- **New default:** `true` (hard block)
- **Example:**
  ```env
  BREAKOUT_BLOCK_IN_RISK_OFF_REGIME=true
  ```
- **Belongs to:** P0 Bug 3 — Stop catching falling knives in broad-market weakness.

---

## P1 Improvements

### `BREAKOUT_ENTRY_SCORE_MIN`
- **Description:** Minimum composite entry score required to enter a position.
- **Old default:** `45.0`
- **New default:** `50.0`
- **Example:**
  ```env
  BREAKOUT_ENTRY_SCORE_MIN=50.0
  ```
- **Belongs to:** P1 Improvement 1 — Reduce false positives by raising the entry bar.

### `BREAKOUT_QUOTE_SCORE_BUFFER`
- **Description:** Buffer applied to the entry threshold to compute the quote floor. Candidates scoring below the floor are not sent for TWAK slippage quotes.
- **Old default:** `5.0` (floor = `threshold - 5` = `40`)
- **New default:** `-3.0` (floor = `threshold + 3` = `53`)
- **Example:**
  ```env
  BREAKOUT_QUOTE_SCORE_BUFFER=-3.0
  ```
- **Belongs to:** P1 Improvement 2 — Only quote candidates that already exceed the entry threshold with margin.
- **Code dependency:** The engine's `_quote_score_floor` property must be updated to `threshold + buffer` (instead of `threshold - buffer`).

### `BREAKOUT_SCORE_WEIGHT_MACRO`
- **Description:** Weight allocated to the macro context factor (total market cap, BTC dominance, stablecoin dominance) in the composite entry score.
- **Old default:** `5.0`
- **New default:** `15.0`
- **Example:**
  ```env
  BREAKOUT_SCORE_WEIGHT_MACRO=15.0
  ```
- **Belongs to:** P1 Improvement 3 — Increase macro voting power in the composite score.
- **Note:** When changing this, re-normalize the full weight table so it sums to 100. See `BREAKOUT_ENGINE_SCORING.md` for the regime-adjusted allocation.

### `FACTOR_MATRIX_LOG_ENABLED`
- **Description:** Write one JSONL row per evaluated symbol per cycle, containing the full factor boolean matrix, raw inputs, entry score, and missing-field flags.
- **Old default:** `false`
- **New default:** `true`
- **Example:**
  ```env
  FACTOR_MATRIX_LOG_ENABLED=true
  ```
- **Belongs to:** P1 Improvement 4 — Enable A/B testing telemetry and offline model training.
- **Disk impact:** ~38 MB/day for 132 symbols at 5-minute cycles. Rotate with `LOG_ROTATE_MAX_MB`.

### `BREAKOUT_MIN_TRUE_FACTOR_COUNT`
- **Description:** Minimum number of true (passing) factors required by the quality-guard layer before entry is approved.
- **Old default:** `3`
- **New default:** `4`
- **Example:**
  ```env
  BREAKOUT_MIN_TRUE_FACTOR_COUNT=4
  ```
- **Belongs to:** P1 Improvement 5 — Require stronger consensus across the six-factor matrix.

### `MAX_UNIVERSE_TWAK_QUOTES` (code constant)
- **Description:** Maximum number of candidates sent for TWAK slippage quotes per cycle.
- **Old value:** `2`
- **New value:** `4`
- **Example:** *(This is a code constant, not an env var. Update in `breakout_engine.py`.)*
  ```python
  MAX_UNIVERSE_TWAK_QUOTES = 4  # was 2
  ```
- **Belongs to:** P1 Improvement 6 — Reduce the chance that a high-scoring symbol is skipped due to quote-slot exhaustion.

---

## Related Unchanged Variables (Reference)

These variables are **not** changing in v2.0 but are frequently referenced alongside the new values.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STRATEGY_MODE` | `breakout` | Must be `breakout` for this scoring system |
| `BREAKOUT_SCORE_WEIGHT_BREAKOUT` | `35.0` | Weight for price breakout strength |
| `BREAKOUT_SCORE_WEIGHT_VOLUME` | `25.0` | Weight for volume surge |
| `BREAKOUT_SCORE_WEIGHT_MOMENTUM` | `15.0` | Weight for momentum z-score |
| `BREAKOUT_SCORE_WEIGHT_RSI` | `10.0` | Weight for RSI component |
| `BREAKOUT_MIN_ENTRY_SCORE_BUFFER` | `0.0` | Optional additional score buffer above threshold |
| `BREAKOUT_ML_MIN_CONFIDENCE` | `0.55` | ML confidence floor (when ML context is provided) |
| `BREAKOUT_BLOCK_IN_CHOP_REGIME` | `true` | Block entries when ML detects chop regime |
| `BREAKOUT_CHOP_CONFIDENCE_BUFFER` | `0.10` | Extra confidence required in chop regime |
| `BREAKOUT_REFERENCE_WINDOWS_HOURS` | `3,6,24` | Lookback windows for reference highs |
| `BREAKOUT_BUFFER` | `0.002` | Minimum clearance above reference high |
| `BREAKOUT_LOOKBACK_HOURS` | `3` | Primary lookback for breakout detection |
| `MAX_SLIPPAGE_PCT` | `0.01` | TWAK slippage cap (1%) |
| `MAX_CHASE_PCT` | `0.04` | Anti-chase cap above broken reference high |
| `FACTOR_MATRIX_LOG_PATH` | `logs/factor_matrix.jsonl` | Output path for factor matrix rows |
| `TRADE_OUTCOME_LOG_PATH` | `logs/trade_outcomes.jsonl` | Output path for entry/exit outcome rows |

---

## Quick Copy-Paste Block

```env
# --- Breakout Engine v2.0 (P0 fixes + P1 improvements) ---

# P0 Bug Fixes
DERIVATIVES_NEUTRAL_ON_MISSING=false
BREAKOUT_SCORE_WEIGHT_DERIVATIVES=0.0
BREAKOUT_REQUIRE_RSI_IN_RANGE=true
BREAKOUT_BLOCK_IN_RISK_OFF_REGIME=true

# P1 Improvements
BREAKOUT_ENTRY_SCORE_MIN=50.0
BREAKOUT_QUOTE_SCORE_BUFFER=-3.0
BREAKOUT_SCORE_WEIGHT_MACRO=15.0
FACTOR_MATRIX_LOG_ENABLED=true
BREAKOUT_MIN_TRUE_FACTOR_COUNT=4

# (Code constant) MAX_UNIVERSE_TWAK_QUOTES = 4 in breakout_engine.py
```

---

*For scoring formula details, see `BREAKOUT_ENGINE_SCORING.md`. For deployment procedures, see `BREAKOUT_ENGINE_MIGRATION.md`.*
