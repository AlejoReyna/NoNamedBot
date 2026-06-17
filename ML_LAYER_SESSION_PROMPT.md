# ML Layer — Session Context Prompt

Start here when picking up work on the cascade-ai ML layer. This prompt summarizes the current state, existing code, and the path to developing and training ML-driven trading signals.

## Project
- **Repo**: `/Users/alexis/Documents/BNBHacks/cascade-ai`
- **Dashboard**: `/Users/alexis/Documents/BNBHacks/cascade-dashboard/cascade-ai-dashboard`
- **Runtime**: Python bot (`src/main.py --live`) with a Next.js dashboard that consumes `/api/status`.

## Current state of live trading

Entry decisions are currently **rule-based**, not ML-based.

- Strategy: **breakout** (default) and **scalping** modes.
- Entry gate: `entry_score >= breakout_entry_score_min` (default `45.0`).
- Score formula (breakout): weighted sum of
  - `breakout_strength`
  - `volume_surge_score`
  - `momentum_z_score`
  - `rsi_in_range`
  - `derivatives_risk_clear`
  - `macro_score`
- File: `src/strategy/6falgorithm/breakout_engine.py` (`_entry_score`, lines ~650-658).
- Per-decision telemetry already surfaces: `entry_score`, `factor_scores`, `factor_metrics`, `true_factor_count`, `strategy_mode`, `reason`.

## ML code that exists today

### 1. Trained-model shadow predictor
- **File**: `src/strategy/model_predictor.py`
- Loads a `joblib` artifact via `src.ml.model_store.load_artifact()`.
- Returns `JumpModelResult(state, confidence, score, numeric_features, source="SHADOW")`.
- Uses `predict_proba` when available; threshold default `0.55`.
- **Status**: only runs when `ENABLE_MODEL_SHADOW=true`; output is logged to `logs/decision_shadow.jsonl` and **not** used in live decisions.
- **Wiring**: `src/main.py:1012-1022` creates it; `src/research/shadow_decisions.py` writes shadow rows.

### 2. Volume-breakout ML tuning
- **File**: `src/strategy/6falgorithm/breakout_engine.py` (~701-712)
- Settings: `ml_volume_breakout_multiplier`, `ml_volume_cache_multiplier`.
- This is the **only ML touch currently active in live entry logic** — it adjusts volume-surge detection thresholds. It is a configuration multiplier, not a learned per-decision signal.

### 3. ML bundle / regime classifier / candidate ranker
- **Files**:
  - `src/ml/regime_predictor.py` — LightGBM/CatBoost/XGBoost classifier with `predict_proba`.
  - `src/ml/bundle.py` — `MLBundle.from_settings()`, `build_contexts()`, `shadow_audit_fields()`.
  - `src/ml/candidate_ranker.py` — `CandidateRanker.rank()` selects among passing candidates using `ml_context.confidence`.
- **Status**: built but **not wired into `main.py`**.
- `Settings.ml_enabled` defaults to `false` and is **never read** in `src/`.
- `_evaluate_universe_v25` accepts an `ml_bundle` kwarg, but the actual call in `main.py` does not pass it.
- Default model path `models/regime_lgbm_v1.pkl` does not exist; only `.meta.json` files and `validation_auc_v2.txt` are present in `models/`.

## Settings

Relevant env vars / settings fields:

- `ML_ENABLED` (`settings.ml_enabled`, default `false`) — intended master switch; currently unused.
- `ENABLE_MODEL_SHADOW` (`settings.enable_model_shadow`, default `false`) — enables shadow logging only.
- `breakout_entry_score_min` — rule threshold.
- `breakout_score_weight_*` — rule weights.
- `ml_volume_breakout_multiplier`, `ml_volume_cache_multiplier` — live volume tuning.

See `src/config/settings.py`.

## Dashboard / telemetry considerations

- The dashboard (`cascade-ai-dashboard`) consumes `StatusPayload` from `/api/status`.
- Both `agent-exporter/src/schemas.ts` and `apps/web/src/lib/schemas.ts` use `.passthrough()` for decisions, so **extra fields written to `decision_log.jsonl` will reach the dashboard automatically**.
- The exporter currently reads `decision_log.jsonl`, `execution_log.jsonl`, `agent.log`, etc., but **does not read `logs/decision_shadow.jsonl`**.
- If ML audit fields are added to live decision records, the dashboard can surface them without schema changes (although adding them to `decisionSchema` improves type safety).

## Open goals for this session

1. **Assess**: determine what ML signal should actually influence entries (regime, ranker, trained jump model, or a blend).
2. **Wire**: connect the existing ML bundle or model predictor into the live decision path in a safe, fail-closed way.
3. **Emit**: add ML audit fields to `decision_log.jsonl` so the dashboard can display ML influence.
4. **Train**: produce or refresh model artifacts from historical data.
5. **Validate**: backtest / shadow-test before enabling live influence.

## Safety constraints

- The bot trades real funds in `--live` mode. Any ML integration must be **fail-closed**: if the model is missing, invalid, or uncertain, the system must fall back to the current rule-based behavior.
- Prefer shadow/logging mode first, then gated live enablement via a setting.
- Preserve the existing guardrails (`guardrail_state.json`, daily loss limits, kill switch).

## Key files to read first

- `src/main.py` — main loop and decision orchestration.
- `src/strategy/6falgorithm/breakout_engine.py` — current entry scoring.
- `src/strategy/model_predictor.py` — trained shadow model.
- `src/research/shadow_decisions.py` — shadow logging.
- `src/ml/bundle.py`, `src/ml/regime_predictor.py`, `src/ml/candidate_ranker.py` — ML bundle.
- `src/execution/decision_log.py` — decision record format.
- `src/config/settings.py` — configuration.
- `agent-exporter/src/telemetry.ts` and `apps/web/src/lib/schemas.ts` — dashboard ingestion.

## Suggested first tasks

- [ ] Read the files above and confirm the current wiring.
- [ ] Decide whether to activate the existing ML bundle, the trained shadow model, or both.
- [ ] Add ML audit fields to live decision records (even if the model is not yet used) so the dashboard can show ML state.
- [ ] Implement a safe live integration path with a feature flag and fallback.
- [ ] Define a training pipeline to generate model artifacts from `decision_log.jsonl` / `execution_log.jsonl` / price and volume caches.
