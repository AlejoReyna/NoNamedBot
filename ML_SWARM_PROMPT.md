# ML Layer — Finish Swarm Prompt

## Context

The previous session wired the ML bundle into the live decision path and added ML audit fields to `decision_log.jsonl`. The work was cut off while fixing CMC feature extraction. **420 tests pass (6 new, 3 pre-existing failures).**

## Repos

- **Backend**: `/Users/alexis/Documents/BNBHacks/cascade-ai` (branch `refactor`)
- **Dashboard**: `/Users/alexis/Documents/BNBHacks/cascade-dashboard/cascade-ai-dashboard` (branch `refactor`)

## What was already done (DON'T redo)

### Live wiring
- `src/main.py` has `_build_ml_bundle()` that creates `MLBundle` when `ML_ENABLED=true`
- `run_agent` creates `ml_bundle` and passes it to `_evaluate_universe_v25()` and `_telemetry_candidate_for_log()`
- `src/strategy/6falgorithm/evaluator.py` `evaluate_universe_breakout()` accepts `ml_bundle`, builds per-symbol `MLContext`, and uses `CandidateRanker` when ranking is active
- `src/strategy/6falgorithm/breakout_engine.py` `evaluate_all()`/`evaluate_universe()` accept `ml_contexts` and attach them to `BreakoutDecision`
- `BreakoutDecision` and `EntryCandidate` both have `ml_audit` field
- `log_decision()` in `src/execution/decision_log.py` writes `ml_audit` to `decision_log.jsonl`
- `src/strategy/candidate_adapter.py` carries `ml_audit` through adapters

### Tests
- `tests/test_main_ml_integration.py` — 6 tests covering bundle forwarding, fail-closed, ML audit, ranking, context-build fallback
- `tests/test_decision_log.py` — test for `ml_audit` field writing
- All tests pass: `pytest tests/test_main_ml_integration.py tests/test_decision_log.py`

### CMC snapshot fields (partially done)
- `src/data/cmc_mcp_client.py` `_build_enriched_snapshot()` now extracts:
  - `volume_change_24h`, `market_cap_dominance`, `fully_diluted_market_cap`
  - `circulating_supply`, `total_supply`, `max_supply`
  - `social_dominance`, `social_volume_change_24h`
  - `fear_greed_index` (parameter accepted, but **NOT being passed from caller**)

### Model artifacts exist
- `models/regime_lgbm_v1.pkl` — synthetic-trained, AUC ~0.48 (below 0.65 threshold)
- `models/regime_lgb_v2.meta.json` — meta file with validation info

## Open tasks — finish these

### Task 1: Wire fear_greed into the CMC snapshot

**Problem**: `fetch_x402_enriched_snapshot()` does NOT pass `fear_greed_index` to `_build_enriched_snapshot()`. The `SentimentTier1` fetches it from a free keyless CMC API, but it's never injected into the market snapshot.

**File**: `src/data/cmc_mcp_client.py`

**What to do**:
1. Add a `fear_greed_index` fetch to `fetch_x402_enriched_snapshot()` (or add a separate method)
2. Pass it to `_build_enriched_snapshot(..., fear_greed_index=...)`
3. The fear_greed source is `SentimentTier1.get_fear_greed()` (free keyless CMC API `/v3/fear-and-greed/latest`)

**Alternative**: Fetch fear_greed directly inside `fetch_x402_enriched_snapshot()` using a keyless call to the CMC fear-greed endpoint, or call `SentimentTier1` if available.

**Safety**: If fear_greed fetch fails, pass `None` — `_build_enriched_snapshot` handles it gracefully.

### Task 2: Expand MLFeatureCache to record ALL new CMC fields

**Problem**: `MLFeatureCache.record_cmc_metrics()` only writes 4 columns: `timestamp, symbol, fear_greed_index, funding_rate`. The new snapshot fields (social_dominance, social_volume_change_24h, market_cap_dominance, etc.) are silently dropped. This means `fear_greed_delta_1d` and other CMC-derived features are dead.

**File**: `src/data/ml_feature_cache.py`

**What to do**:
1. Add columns to the `cmc_metrics` table schema:
   - `social_dominance` (REAL)
   - `social_volume_change_24h` (REAL)
   - `market_cap_dominance` (REAL)
   - `volume_change_24h` (REAL)
   - `open_interest_change_pct` (REAL)
2. Update `record_cmc_metrics()` to insert these fields
3. Update `get_fear_greed_prior()` to also get `fear_greed_delta_1d` (difference between current and prior ~24h ago)
4. Add `get_cmc_metric_history(symbol, metric, days)` method for z-score computation
5. Write a migration so existing SQLite DBs don't break (add columns if not exist, or recreate table)

**Safety**: The bot should NOT crash if the cache table is missing columns. Use `PRAGMA table_info` to check columns, or use `ALTER TABLE ADD COLUMN`.

### Task 3: Extend historical data fetcher for 149 tokens + longer history

**Problem**: `scripts/fetch_historical_data.py` only fetches 30 days for `settings.ml_universe_symbols` (default 4 symbols: BNB, CAKE, ETH, BTC). For training a usable model, we need 6-12 months of 15m OHLCV for the full 149-token universe.

**File**: `scripts/fetch_historical_data.py`

**What to do**:
1. Accept a `--symbols` arg (default: all 149 from `ELIGIBLE_149_SYMBOLS`)
2. Accept a `--days` arg (default: 180 = 6 months)
3. Accept a `--workers` arg for parallel fetching (default: 4)
4. Handle Binance rate limits with `time.sleep()` between requests
5. Skip symbols that Binance doesn't have (404 errors)
6. Skip stablecoins (USDT, USDC, DAI, USDe, USD1, etc.) and gold-backed (XAUt) — they don't give directional signal
7. Save progress so rerunning resumes from where it left off (manifest with last fetched timestamp per symbol)
8. The CMC snapshot loop should collect daily snapshots for the same date range

**Files to reference**:
- `src/config/tokens.py` for `ELIGIBLE_149_SYMBOLS`
- `src/data/binance_client.py` for `fetch_history_days()`
- `src/data/cmc_mcp_client.py` for `fetch_x402_enriched_snapshot()`

**Test**: Run the script and verify parquet files exist for >100 symbols.

### Task 4: Build a real feature matrix + train model

**Problem**: The synthetic model has AUC 0.48 (below 0.65 threshold). Real data should produce a better model.

**Files**: `scripts/build_feature_matrix.py`, `scripts/train_regime_model.py`

**What to do**:
1. After Task 3 completes, run:
   ```bash
   python scripts/build_feature_matrix.py
   python scripts/train_regime_model.py
   ```
2. Check the worst-fold AUC in `models/validation_auc_v2.txt`
3. If AUC < 0.65, try:
   - Restricting to symbols with cleaner signal (filter by volatility or volume)
   - Adding more CMC features (Task 2 must be done first)
   - Tuning `label_horizon` (default 16 candles = 4h, try 4-24 candles)
4. Once AUC >= 0.65, the model will automatically enable live ranking via `ml_min_auc` check

**Note**: If the user hasn't run Task 3 yet, just verify the scripts work with the existing data. Don't generate synthetic data.

### Task 5: Dashboard ML audit display

**Problem**: The dashboard doesn't show ML influence fields even though they're now in `decision_log.jsonl`.

**File**: `cascade-dashboard/cascade-ai-dashboard/apps/web/src/lib/schemas.ts` and `apps/web/src/components/dashboard-client.tsx`

**What to do**:
1. Add optional `mlAudit` field to `decisionSchema` in `schemas.ts`:
   ```typescript
   mlAudit: z.object({
     mlEnabled: z.boolean().optional(),
     mlActive: z.boolean().optional(),
     mlShadowMode: z.boolean().optional(),
     mlValidationAuc: z.number().optional(),
     mlRegime: z.string().optional(),
     mlConfidence: z.number().optional(),
     mlPositionSizeMultiplier: z.number().optional(),
     mlPasserCount: z.number().optional(),
     mlPasserSymbols: z.array(z.string()).optional(),
   }).optional(),
   ```
2. Add a small "ML" indicator to the decision detail panel in `dashboard-client.tsx` (or the existing Entry Reason area)
   - Show: `ML: {regime} | confidence: {confidence}% | ranking: {passerCount} passers`
   - Only show if `mlAudit` is present
   - Color: green if `mlActive`, yellow if `mlEnabled` but `mlActive=false`, gray if absent
3. The dashboard already uses `.passthrough()` for decisions, so extra fields reach the frontend automatically. Adding to `decisionSchema` is for type safety only.

## Safety constraints (apply to ALL tasks)

- The bot trades real funds. Any ML integration must be **fail-closed**: if model is missing/invalid/uncertain, fall back to rule-based behavior.
- `ml_enabled=false` → no behavior change.
- Model AUC < 0.65 → ranking stays inactive.
- `ml_shadow_mode=true` → ML audit fields are emitted but do NOT influence live decisions.
- Preserve existing guardrails (`guardrail_state.json`, daily loss limits, kill switch).
- All changes must pass existing tests. Add new tests for new functionality.

## How to run tests

```bash
cd /Users/alexis/Documents/BNBHacks/cascade-ai
source .venv/bin/activate
pytest tests/test_main_ml_integration.py tests/test_decision_log.py -v
pytest tests/test_breakout_engine.py -v
pytest  # full suite
```

## Suggested agent swarm split

- **Agent A** (CMC features): Tasks 1 + 2 — fear_greed injection + MLFeatureCache expansion
- **Agent B** (Data pipeline): Task 3 — extend fetch_historical_data.py for 149 tokens
- **Agent C** (Training): Task 4 — build feature matrix, train model, verify AUC
- **Agent D** (Dashboard): Task 5 — add ML audit display to dashboard

## Known issues from previous session

1. `test_emergency_liquidate_caps_to_live_wallet_balance` — pre-existing failure
2. `test_build_dataset_joins_entry_exit_and_excludes_exit_features` — pre-existing failure  
3. `test_exit_swap_caps_to_live_wallet_balance` — pre-existing failure

These are NOT caused by ML changes. Do not spend time fixing them unless asked.

## Quick verification checklist after swarm completes

- [ ] `fetch_x402_enriched_snapshot()` passes `fear_greed_index` to `_build_enriched_snapshot()`
- [ ] `MLFeatureCache` table has new columns and records all CMC fields
- [ ] `fear_greed_delta_1d` can be computed from cache history
- [ ] `scripts/fetch_historical_data.py` can fetch 6 months for 100+ symbols
- [ ] `scripts/train_regime_model.py` produces model with AUC >= 0.65 (or close, with plan to improve)
- [ ] Dashboard shows ML audit fields when present in decisions
- [ ] Full test suite: `pytest` passes (420+, same 3 pre-existing failures)
- [ ] `ml_enabled=false` path still works (no ML bundle created, no behavior change)
