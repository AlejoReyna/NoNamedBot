# ML Implementation — Current State & Next Steps

_Last updated: 2026-06-17_

This doc captures where the shadow-first ML layer stands and what to do next. The
guiding principle is unchanged: **the deterministic rule-based engine remains the
live decision-maker.** The model runs in shadow, must beat the rule engine after
fees on held-out data, and only ever gets *advisory* influence later — never
unchecked control.

---

## 1. What exists today

### Offline research / training (pandas, runs off the trading box)
- **`src/research/dataset.py`** — `build_dataset()` joins entry/exit events from
  `logs/trade_outcomes.jsonl` by `trade_id` (with a symbol/time fallback),
  flattens `factor_scores` into `factor_*` columns, as-of joins CMC quote
  features (`direction="backward"`, no future leakage), and labels
  `entry_win = realized_pnl_usdc > 0`.
- **`src/research/train.py`** — walk-forward (`TimeSeriesSplit`) training of a
  LogReg baseline and optional LightGBM. Scores **simulated PnL vs. the rule
  engine** (model can only *filter* rule entries — veto framing). Exports a
  joblib artifact + a `model_card.json` with git SHA, metrics, and a promotion
  gate.
- **`src/research/feature_contract.py`** — dependency-free single source of truth
  for feature names. `entry_feature_vector()` builds the canonical feature dict
  used by **both** training and live shadow, so feature names can never drift
  apart (no train/serve skew). Deliberately pandas-free so the live box doesn't
  need ML deps to build shadow features.

### Live shadow serving (fail-closed, zero capital risk)
- **`src/strategy/model_predictor.py`** — loads the artifact once; if it's
  missing/unloadable it disables itself and returns a neutral result. Scores via
  name-based feature lookup against the artifact's `feature_names`.
- **`src/research/shadow_decisions.py`** — each cycle logs a `trained_model`
  variant to `logs/decision_shadow.jsonl`, scoring the cycle's actual
  **candidate** through `entry_feature_vector`. Logged only when (a) a candidate
  exists, (b) a predictor is present, and (c) `ENABLE_MODEL_SHADOW=true`. Returns
  `None` to the live caller — strict shadow isolation preserved.
- **`src/main.py`** — passes `candidate=candidate` into `log_all_variants`,
  wrapped in try/except so shadow can never take down a trading cycle.
- **`src/config/settings.py`** — `enable_model_shadow` (default `False`),
  `model_shadow_path`, `model_shadow_threshold`, all env-driven.

### Data the model trains on
- `logs/trade_outcomes.jsonl` — entry factors + realized PnL (the **labels**).
- `data/cmc_premium.db` — CMC quote features, written by
  `scripts/cmc_feature_collector.py` every 30 min (systemd timer).

### Tests
- `tests/test_research_dataset.py` — join/label correctness, leakage rejection,
  and a **train/serve feature-parity** guard.
- `tests/test_model_predictor_shadow.py` — fail-closed behavior, candidate-only
  logging, and that the model is scored on `factor_*`/`entry_score` features.

---

## 2. Recent fix (important context)

The shadow predictor was originally fed four BNB regime proxies
(`momentum_10`, …) while the artifact expected `factor_*`/`entry_score`
features — so it silently scored an **all-zeros vector every cycle**, making the
shadow comparison meaningless. Fixed by routing both training and shadow through
the single `entry_feature_vector` contract, restricting `feature_columns` to a
**reproducible whitelist** (`factor_*`, `entry_score`, `true_factor_count`),
which also removed `opened_at`/`entry_price`/`size_usdc` leakage, and tightening
`assert_no_leakage` to reject anything outside that whitelist. A parity test now
guards against the regression.

Verification: affected + neighboring tests pass (dataset, shadow, settings,
model-predictor). The full suite was **not** re-run in the dev sandbox (missing
heavy runtime deps: web3/mcp/x402; repo `.venv` is macOS-only) — run
`pytest -q` in a proper env to confirm all ~410 cases.

---

## 3. Known gaps / open items

1. **No model artifact yet.** Intentionally not generated — too few closed-trade
   labels would produce a junk model. Pipeline is ready once data accumulates.
2. **CMC features excluded from the model set.** They live in the dataset for
   analysis but are *not* model features, because the live snapshot doesn't
   reproduce the collector DB schema 1:1. Re-add only via a shared
   snapshot→feature builder feeding both paths.
3. **PnL gate can be gamed by "trade nothing."** A model that selects zero trades
   in a losing fold scores `model_pnl=0 > rule_pnl` and passes. Add a
   `selected_trades >= N` floor to the promotion gate. _(Not yet done.)_
4. **Two parallel ML stacks.** An older `src/ml/` regime pipeline
   (`regime_lgbm_v1`, settings `ml_enabled`/`ml_model_path`/`ml_min_auc`) sits
   next to the new `src/research/` entry-quality work. Only the latter is wired
   into shadow. Decide which is canonical; mark/remove the other.
5. **Runtime deps on the live box.** `joblib.load` unpickles the sklearn/LightGBM
   object, so serving a *real* artifact requires `requirements-ml` on EC2. Either
   install it there (heavier runtime) or export to a dependency-light format
   (ONNX / raw LightGBM text booster). Otherwise the predictor fail-closes in
   prod and shadow silently won't run.

---

## 4. Suggested next steps (in order)

1. **Accumulate labels.** Let the bot run so closed trades build up in
   `trade_outcomes.jsonl`. The entry-quality model needs weeks of closed trades;
   a regime model can train sooner.
2. **Harden the promotion gate** — add the `selected_trades >= N` floor (#3).
3. **First training run** once `entry_win` has both classes and enough rows;
   inspect `model_card.json` (AUC, `pnl_delta_usdc`, gate result). Do **not**
   deploy if it doesn't beat the rule baseline after fees.
4. **Turn on shadow** (`ENABLE_MODEL_SHADOW=true`) with the artifact present and
   `requirements-ml` installed on the box. Run 2–4 weeks.
5. **Compare** shadow model calls vs. live rule decisions vs. realized outcomes
   using `scripts/replay_shadow.py` / `ab_test_runner.py`. Confirm shadow PnL
   roughly matches the offline backtest (large divergence = leakage/stale model).
6. **Only if it wins:** promote to *advisory* influence (confidence multiplier or
   veto on the breakout scorer / sizing) — guardrails keep veto power, and a
   missing/stale artifact must auto-revert to pure rule-based behavior.
7. **Consolidate the two ML stacks** (#4) before the codebase confuses the next
   engineer.

---

## 5. Data / storage direction (RDS question)

For the current single-box, append-only, offline-training setup, **start with
S3 + DuckDB**, not RDS:
- Keep the live loop writing local JSONL/SQLite exactly as-is.
- Async, best-effort shipper (extend the 30-min collector timer) pushes
  `logs/` and `data/` to a private, versioned S3 bucket via the EC2 **instance
  role** — never blocking the trading cycle.
- Train from the S3 copy; `build_dataset` already takes paths, and DuckDB reads
  JSONL/SQLite/Parquet directly.

Move to **RDS Postgres (+ TimescaleDB for the quote time-series)** only when a
real trigger appears: multiple agent instances sharing mutable state, or an
always-on dashboard/API. Even then, feed it from the same async sidecar — the
trading loop must never wait on a network DB. This keeps the fail-closed design
intact and the decision is reversible.

---

## TL;DR
Shadow-first ML scaffolding is complete and correct; the critical feature-skew
bug is fixed and guarded by a parity test. No model is trained yet by design —
collect closed-trade labels, harden the PnL gate, train, run shadow for weeks,
and only then consider advisory influence. For storage, prefer S3 + DuckDB now;
RDS only when multi-instance or live dashboards demand it.
