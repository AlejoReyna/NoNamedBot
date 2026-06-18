# ML Layer — Swarm Execution Plan

## Stage 1 — Parallel Implementation (4 agents)

### Agent A: CMC fear_greed injection + MLFeatureCache expansion (Tasks 1 + 2)
**Files**: `src/data/cmc_mcp_client.py`, `src/data/ml_feature_cache.py`, `tests/test_ml_feature_cache.py`

1. **Task 1**: In `fetch_x402_enriched_snapshot()`, add a keyless fear-greed fetch before calling `_build_enriched_snapshot()`. The endpoint is the keyless CMC API `/v3/fear-and-greed/latest` (already implemented in `SentimentTier1.get_fear_greed()`). Extract the `value` field, normalize it (divide by 100 if > 1), and pass as `fear_greed_index=` to `_build_enriched_snapshot()`. If the fetch fails, pass `None`.

2. **Task 2**: Expand `MLFeatureCache`:
   - Add columns to `cmc_metrics` table: `social_dominance`, `social_volume_change_24h`, `market_cap_dominance`, `volume_change_24h`, `open_interest_change_pct` (all REAL, nullable).
   - Write migration: on `_connect()`, check `PRAGMA table_info(cmc_metrics)` and `ALTER TABLE ADD COLUMN` for any missing columns.
   - Update `record_cmc_metrics()` to insert all new fields.
   - Update `get_fear_greed_prior()` to also return `fear_greed_delta_1d` (current - prior ~24h ago).
   - Add `get_cmc_metric_history(symbol, metric, days)` for z-score computation.
   - Update `test_ml_feature_cache.py` to test new columns and delta computation.

### Agent B: Extend historical data fetcher (Task 3)
**Files**: `scripts/fetch_historical_data.py`

1. Add CLI args: `--symbols` (default all from `ELIGIBLE_149_SYMBOLS`), `--days` (default 180), `--workers` (default 4).
2. Filter out stablecoins and gold-backed tokens (USDT, USDC, DAI, USDe, USD1, XAUt, etc.) before fetching.
3. Implement parallel fetching with `ThreadPoolExecutor` and rate-limit sleep between requests.
4. Handle 404 errors gracefully (skip unavailable symbols).
5. Save progress manifest with last-fetched timestamp per symbol. On rerun, resume from last timestamp.
6. CMC snapshot loop: collect daily snapshots for the same date range using `fetch_x402_enriched_snapshot()`.
7. Test: run the script and verify parquet files exist for >100 symbols.

### Agent C: Feature matrix + model training (Task 4)
**Files**: `scripts/build_feature_matrix.py`, `scripts/train_regime_model.py`

1. Verify `build_feature_matrix.py` works with existing data (if any).
2. Verify `train_regime_model.py` reads the matrix and trains models.
3. Check worst-fold AUC in `models/validation_auc_v2.txt`.
4. If AUC < 0.65 and real data exists, suggest improvements (filter by volatility, tune label_horizon).
5. Do NOT generate synthetic data.

### Agent D: Dashboard ML audit display (Task 5)
**Files**: `cascade-dashboard/cascade-ai-dashboard/apps/web/src/lib/schemas.ts`, `apps/web/src/components/dashboard-client.tsx`

1. Add `mlAudit` optional object to `decisionSchema` in `schemas.ts`.
2. Add ML indicator to `PositionEntryReason` in `dashboard-client.tsx`:
   - Show: `ML: {regime} | confidence: {confidence}% | ranking: {passerCount} passers`
   - Color: green if `mlActive`, yellow if `mlEnabled` but `mlActive=false`, gray if absent.
   - Only show if `mlAudit` is present.

## Stage 2 — Validation
- Run `pytest tests/test_main_ml_integration.py tests/test_decision_log.py -v`
- Run `pytest tests/test_breakout_engine.py -v`
- Run full `pytest` suite (expect 420+ pass, 3 pre-existing failures)
- Verify `ml_enabled=false` path still works

## Safety Constraints
- Fail-closed: any model missing/invalid → fall back to rule-based
- `ml_enabled=false` → no behavior change
- AUC < 0.65 → ranking inactive
- `ml_shadow_mode=true` → audit only, no live influence
- Preserve existing guardrails
