# Breakout Engine Scoring Formula — v2.0

> **Applies to:** `src/strategy/6falgorithm/breakout_engine.py` (post-migration)  
> **Weight table:** Regime-adjusted, sums to 100  
> **Range:** `0.0` — `100.0` (clamped)

---

## Overview

The entry score is a **weighted sum of six continuous or graded factors**. Unlike v1.0, which used binary pass/fail for most factors (RSI, derivatives), v2.0 uses continuous components where possible, and regime-adjusted weights that shift emphasis based on volatility context.

```
entry_score = Σ(weight_i × factor_i)

weights = regime_adjusted(atr_ratio)  # sums to 100
score   = clamp01(score / 100) × 100    # final 0–100 scale
```

---

## Factor Definitions

### 1. `breakout_strength` — Weight 35 → 30 / 20

- **Input:** `candidate.breakout_strength` (computed in `_breakout_profile`)
- **Formula:** `clamp01(strength)` where `strength = cleared_weight / total_weight`
- **Meaning:** Fraction of reference windows (3h, 6h, 24h) whose highs have been cleared by the current price, weighted by window size.
- **Regime shift:**
  - High vol (`atr_ratio > 1.5`): weight **↓** to 20 (breakouts are noisier)
  - Low vol (`atr_ratio < 0.7`): weight **↓** to 30 (breakouts are less meaningful)
  - Base: 30

### 2. `volume_surge_score` — Weight 25 → 20 / 40

- **Input:** `candidate.volume_surge_score` (from `_volume_signal`)
- **Formula:** `clamp01(surge)` where `surge = volume_ratio / breakout_mult`
- **Meaning:** How much the recent 1h volume exceeds the rolling hourly average, normalized by the configured breakout multiplier.
- **Regime shift:**
  - High vol: weight **↓** to 20 (volume already elevated, less signal)
  - Low vol: weight **↑** to 40 (volume is the only confirming signal)
  - Base: 25

### 3. `momentum_z_score` — Weight 15 → 25 / 10

- **Input:** Cross-sectional z-score of 1h + 0.5 × 24h momentum
- **Formula:** `clamp01(z / 2.0)`
- **Meaning:** How far this token's momentum deviates from the universe mean, in standard-deviation units. A z-score of `2.0` maps to `1.0` (full weight).
- **Regime shift:**
  - High vol: weight **↑** to 25 (momentum persistence is stronger in volatile regimes)
  - Low vol: weight **↓** to 10 (momentum is mean-reverting in quiet markets)
  - Base: 15

### 4. `rsi` — Weight 10 → 5 / 15

- **Input:** `candidate.rsi` (from CMC x402 technicals or keyless feed)
- **Formula:** `graded_rsi(rsi)` — continuous, not binary
- **Meaning:** Rewards RSI near the momentum sweet spot (~65) and penalizes extremes.
- **Regime shift:**
  - High vol: weight **↑** to 15 (RSI extremes matter more in trending markets)
  - Low vol: weight **↓** to 5 (RSI chops in range-bound markets)
  - Base: 10

#### Graded RSI curve

```python
def _rsi_component(rsi: float) -> float:
    """
    Peak at 65, zero at 45 & 85, linear between.
    _rsi_component(65) = 1.0
    _rsi_component(55) = 0.5
    _rsi_component(80) = 0.25
    _rsi_component(45) = 0.0
    """
    if rsi is None:
        return 0.0
    if 45.0 <= rsi <= 65.0:
        return (rsi - 45.0) / 20.0
    if 65.0 < rsi <= 85.0:
        return (85.0 - rsi) / 20.0
    return 0.0
```

| RSI | Component |
| --- | --- |
| 45 | 0.00 |
| 50 | 0.25 |
| 55 | 0.50 |
| 60 | 0.75 |
| 65 | 1.00 |
| 70 | 0.75 |
| 75 | 0.50 |
| 80 | 0.25 |
| 85 | 0.00 |

> **Note:** The old v1.0 binary band (`55 ≤ rsi ≤ 75`) is still used by the quality-guard `breakout_require_rsi_in_range`. The graded component adds **continuous resolution** inside the band rather than a flat `1.0`.

### 5. `derivatives` — Weight 10 → 10 / 10

- **Input:** `funding_rate` + `open_interest_change_pct`
- **Formula:** `continuous(funding, OI)` — see below
- **Meaning:** Measures whether the perp market is supporting or contradicting the spot breakout.
- **Regime shift:** Unchanged at 10 across all regimes (derivatives data is orthogonal to volatility).

#### Continuous derivatives formula

```python
def _derivatives_component(
    funding_rate: float | None,
    open_interest_change: float | None,
) -> float:
    """
    missing data      → 0.5 (neutral, until real feed)
    funding = -0.05% → 1.0 (squeeze setup, short squeeze likely)
    funding = +0.05% → 0.0 (overheated, crowded long)
    OI rising +10%   → 1.0 (new money confirming breakout)
    OI falling -10%  → 0.0 (breakout on thinning liquidity)
    """
    if funding_rate is None or open_interest_change is None:
        return 0.5  # neutral — data missing, not bullish or bearish

    funding_score = 1.0 - clamp01((funding_rate + 0.0005) / 0.0010)
    # funding_rate = -0.05% → (-0.0005 + 0.0005) / 0.001 = 0 → 1.0
    # funding_rate = +0.05% → (0.0005 + 0.0005) / 0.001 = 1.0 → 0.0

    oi_score = clamp01((open_interest_change + 10.0) / 20.0)
    # OI = -10% → 0 / 20 = 0.0
    # OI = +10% → 20 / 20 = 1.0

    return 0.6 * funding_score + 0.4 * oi_score
```

> **v2.0 behavior:** When `BREAKOUT_SCORE_WEIGHT_DERIVATIVES=0.0` (no Binance feed yet), this component contributes `0.0` regardless of the continuous value. The slot is reserved for re-activation.

### 6. `macro` — Weight 5 → 10 / 5

- **Input:** `candidate.macro_score` (from `_macro_context`)
- **Formula:** `clamp01(macro_score)`
- **Meaning:** Macro health score derived from total market cap delta, BTC dominance, and stablecoin dominance shifts.
- **Regime shift:**
  - High vol: weight **↑** to 10 (macro trends dominate in volatile regimes)
  - Low vol: weight **↓** to 5 (idiosyncratic token moves matter more)
  - Base: 5

---

## Weight Regime Adjustment

```python
def regime_adjusted_weights(atr_ratio: float) -> dict[str, float]:
    """
    atr_ratio = current_ATR / 30d_ATR
    """
    base = {
        "breakout": 30.0,
        "volume": 25.0,
        "momentum": 15.0,
        "rsi": 10.0,
        "derivatives": 10.0,
        "macro": 10.0,
    }

    if atr_ratio > 1.5:        # high volatility
        return {
            "breakout": 20.0,  # noise ↑
            "volume": 20.0,    # already elevated
            "momentum": 25.0,  # persistence ↑
            "rsi": 15.0,       # extremes matter
            "derivatives": 10.0,
            "macro": 10.0,     # macro trends dominate
        }
    elif atr_ratio < 0.7:      # low volatility
        return {
            "breakout": 30.0,  # less meaningful
            "volume": 40.0,    # only confirming signal
            "momentum": 10.0,  # mean reversion
            "rsi": 5.0,        # chop
            "derivatives": 10.0,
            "macro": 5.0,      # idiosyncratic > macro
        }
    else:
        return base            # base regime
```

| Regime | `atr_ratio` | Breakout | Volume | Momentum | RSI | Derivatives | Macro | **Sum** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| High vol | > 1.5 | 20 | 20 | 25 | 15 | 10 | 10 | **100** |
| Base | 0.7 – 1.5 | 30 | 25 | 15 | 10 | 10 | 10 | **100** |
| Low vol | < 0.7 | 30 | 40 | 10 | 5 | 10 | 5 | **100** |

> **Implementation note:** `atr_ratio` is computed from the token's own 14-period ATR versus its 30-day median ATR. If ATR data is unavailable, the engine falls back to **base weights**.

---

## Sentiment Modifiers (post-weight)

After the weighted sum, two sentiment modifiers are applied **additively** (not multiplicatively):

```python
# Token-specific sentiment modifiers (CMC MCP news + narratives)
if token_sentiment:
    if token_sentiment.get("news_bearish_last_4h"):
        score -= 10.0
    if token_sentiment.get("kol_bullish") and token_sentiment.get("funding_neutral"):
        score += 5.0
```

| Modifier | Condition | Δ Score |
| --- | --- | --- |
| Bearish news | `news_bearish_last_4h=True` | **−10.0** |
| KOL bullish + funding neutral | `kol_bullish=True` and `funding_neutral=True` | **+5.0** |

The final score is clamped to `[0.0, 100.0]`:

```python
return max(0.0, round(score, 4))
```

---

## Quality Guards (post-score)

The raw score is necessary but not sufficient. These guards are applied **after** scoring and can override `should_enter`:

| Guard | Setting | Behavior |
| --- | --- | --- |
| Min true factor count | `BREAKOUT_MIN_TRUE_FACTOR_COUNT=4` | Require ≥ 4 of 6 factors true |
| Risk-off block | `BREAKOUT_BLOCK_IN_RISK_OFF_REGIME=true` | Block when regime is risk-off |
| RSI gate | `BREAKOUT_REQUIRE_RSI_IN_RANGE=true` | Block when RSI missing or outside 55–75 |
| Score buffer | `BREAKOUT_MIN_ENTRY_SCORE_BUFFER=0.0` | Optional: require `score ≥ threshold + buffer` |
| ML confidence | `BREAKOUT_ML_MIN_CONFIDENCE=0.55` | Block when ML confidence is below threshold (chop regime) |
| Chop block | `BREAKOUT_BLOCK_IN_CHOP_REGIME=true` | Block when ML detects chop and confidence is low |

---

## Example Score Walkthrough

**Scenario:** Token X in **base regime** (`atr_ratio = 1.0`)

| Factor | Raw Input | Component | Weight | Weighted |
| --- | --- | --- | --- | --- |
| Breakout | strength = 0.85 | 0.85 | 30 | 25.50 |
| Volume | surge = 2.4× | 1.00 (capped) | 25 | 25.00 |
| Momentum | z = 1.2 | 0.60 | 15 | 9.00 |
| RSI | rsi = 62 | 0.85 | 10 | 8.50 |
| Derivatives | funding = -0.02%, OI = +5% | 0.70 | 10 | 7.00 |
| Macro | macro_score = 0.40 | 0.40 | 10 | 4.00 |
| **Subtotal** | | | | **79.00** |
| Sentiment | no bearish news | — | | +0.00 |
| **Final** | | | | **79.0** |

**Decision:** `79.0 ≥ 50.0` threshold → passes score guard. If 4+ factors are true and regime is not risk-off, entry is approved.

---

## Migration Notes from v1.0

| v1.0 Behavior | v2.0 Behavior |
| --- | --- |
| RSI: binary `1.0` if in 55–75, else `0.0` | RSI: graded curve peaking at 65 |
| Derivatives: binary `1.0` if clear, else `0.0` | Derivatives: continuous `0.0–1.0` based on funding + OI |
| Weights: static | Weights: regime-adjusted by `atr_ratio` |
| Score floor: `threshold - buffer` (5.0) | Score floor: `threshold + buffer` (−3.0) |
| Quote slots: 2 | Quote slots: 4 |

---

*See `BREAKOUT_ENGINE_MIGRATION.md` for the full migration guide and deployment checklist.*
