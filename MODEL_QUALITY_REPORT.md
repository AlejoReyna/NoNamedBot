# Model Quality Report

Generated: 2026-06-17T23:24:04.153415+00:00

## Summary
- Positive class rate: **22.6%**
- Feature count: **41**
- Best model: **lgb**
- Recommendation: **KEEP shadow/regime-only fallback — worst-fold AUC below 0.65; ML ranking disabled.**

## Per-model purged CV (5 folds, 24-candle purge gap)

| Model | Mean AUC | Std | Worst-fold AUC | Folds |
|-------|----------|-----|----------------|-------|
| lgb | 0.5794 | 0.0459 | 0.5194 | 0.583, 0.536, 0.519, 0.637, 0.621 |

## Best model feature importance (top 10)

- `volatility_48`: 1981.0000
- `range_compression_6h`: 1420.0000
- `volatility_16`: 1335.0000
- `bnb_corr_48`: 1332.0000
- `volume_price_divergence`: 1280.0000
- `bnb_beta_48`: 1247.0000
- `hour_of_day`: 1229.0000
- `ema_8_21_spread`: 1167.0000
- `atr_pct_14`: 1138.0000
- `volume_skew_3h_6h`: 1105.0000

## Shadow mode recommendation

KEEP shadow/regime-only fallback — worst-fold AUC below 0.65; ML ranking disabled.

Set `ML_SHADOW_MODE=false` only after worst-fold AUC >= 0.65 and 48h shadow paper validation.
