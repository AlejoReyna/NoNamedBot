# CASCADE-AI Breakout Engine — Swarm Coordination Spec

## Goal
Fix 4 P0 bugs and implement 5 P1 scoring improvements in `src/strategy/6falgorithm/breakout_engine.py` with full test coverage and documentation.

## Non-Goals
- Scalping engine (`scalping_engine.py`) is out of scope.
- No on-chain execution changes; TWAK interface stays untouched.
- No ML model retraining.

## Repo Facts
- Python 3.12, uses `pytest` for tests.
- Main module: `src/strategy/6falgorithm/breakout_engine.py` (1169 lines).
- Compatibility shim: `src/strategy/breakout_engine.py` re-exports from the 6falgorithm module. Do NOT edit the shim.
- Settings: `src/config/settings.py` via Pydantic `Settings` class; `load_settings()` reads env vars.
- Tests: `tests/test_breakout_engine.py` imports from `src.strategy.breakout_engine` (shim). All tests must pass after merge.
- Baseline branch: `agent/breakout-swarm-base` (commit `daf4af4`).

## Shared Contract
### Files All Agents May Read
- `src/strategy/6falgorithm/breakout_engine.py`
- `src/config/settings.py`
- `tests/test_breakout_engine.py`

### Interfaces Must Not Change (without orchestrator approval)
- `BreakoutDecision` dataclass fields (additions allowed, removals forbidden).
- `BreakoutEngine.evaluate_token()`, `evaluate_all()`, `evaluate_universe()` signatures.
- `TWAKInterface.estimate_slippage_pct()` signature.
- `Settings` class field names used by other modules.

### Allowed Additions
- New private methods on `BreakoutEngine` (prefixed with `_`).
- New fields in `_CheapCandidate` (frozen dataclass — add with defaults).
- New env vars in `settings.py` with `BREAKOUT_` prefix.
- New test functions.
- New documentation files.

## Task Slices

### Agent 1 — Config & Bug Fix Squad
**Worktree:** `/Users/alexis/Documents/BNBHacks/.worktrees/agent1-config-bugs`
**Branch:** `agent/agent1-config-bugs`

**Implement:**
1. **Bug #1 (derivatives_neutral_on_missing):** In `_evaluate_cheap_candidate` (lines 291–302), change the missing-data path to award a **continuous 0.5 score** instead of boolean `True`. Specifically: when `funding_rate` or `open_interest_change` is missing, set `derivatives_risk_clear = True` but add a new `derivatives_score: float = 0.5` field to `_CheapCandidate`. Update `_entry_score` to use `candidate.derivatives_score` (float) instead of binary `1.0 if candidate.derivatives_risk_clear else 0.0`. When data IS present, `derivatives_score = 1.0 if derivatives_risk_clear else 0.0`. This is the minimal fix that stops free 10-point inflation while keeping the existing behavior for present data.
2. **Bug #2 (momentum_z_score cache):** In `__init__`, add `self._last_momentum_z_scores: dict[str, float] = {}`. In `evaluate_token` (line 245), use `cached_z = self._last_momentum_z_scores.get(symbol, 0.0)` and pass `momentum_z_score=cached_z`. In `evaluate_all` (after line 665), add `self._last_momentum_z_scores = momentum_scores`.
3. **Bug #3 (quote floor / TWAK slots):** Change `MAX_UNIVERSE_TWAK_QUOTES` from `2` to `4`. Change `_quote_score_floor` from `threshold - buffer` to `threshold + 3.0` (hardcoded above threshold to stop wasted quotes). Update `_should_quote_candidate` accordingly.
4. **Bug #4 (dead min_entry_factors):** In `settings.py`, add `breakout_min_true_factor_count = min_entry_factors` alias in `load_settings()` so the engine respects the setting. OR change `breakout_min_true_factor_count` default to `4` to match `min_entry_factors`. Prefer: in `load_settings`, after building `values`, add `values["breakout_min_true_factor_count"] = values.get("min_entry_factors", 3)`.

**Validation:**
- `pytest tests/test_breakout_engine.py -v` must pass.
- Commit all changes in the worktree.

---

### Agent 2 — Scoring Model Refactor Squad
**Worktree:** `/Users/alexis/Documents/BNBHacks/.worktrees/agent2-scoring`
**Branch:** `agent/agent2-scoring`

**Implement:**
1. **Improvement #1 (Graded RSI):** Add `_rsi_component(self, rsi: float | None) -> float` method. Smooth bell curve: center at 65, zero at 45 and 85, linear decay. Update `_entry_score` to use `self._rsi_component(candidate.rsi)` instead of binary.
2. **Improvement #3 (Dynamic Weighting):** Add `_regime_adjusted_weights(self, atr_ratio: float) -> dict[str, float]`. Base weights: breakout=35, volume=25, momentum=15, rsi=10, derivatives=10, macro=5. High vol (>1.5): breakout=30, volume=20, momentum=25, rsi=5, derivatives=10, macro=10. Low vol (<0.7): breakout=20, volume=40, momentum=10, rsi=15, derivatives=10, macro=5. Re-normalize to sum 100. Update `_entry_score` to call `_regime_adjusted_weights` and use returned weights. Note: `_entry_score` must accept an `atr_ratio` parameter or compute it from candidate. For now, add `atr_ratio: float = 1.0` parameter with default.
3. **Improvement #4 (Macro Expansion):** Expand `_macro_context` to include:
   - `stablecoin_market_cap_slope` (new key in token_data: `macro_stablecoin_market_cap`)
   - `defi_market_cap_delta` (new key: `macro_defi_market_cap`)
   - New scoring: total=0.25, btc=0.20, stable_dom=0.15, stable_slope=0.25, defi=0.15. Total must sum to 1.0.
   - Raise `breakout_score_weight_macro` default to 15.0 in `settings.py` (add to `load_settings` default).
4. Update `_entry_score` to use the new methods. Keep the derivatives part as binary for now (Agent 3 will replace it).

**Validation:**
- `pytest tests/test_breakout_engine.py -v` must pass (existing tests).
- Commit all changes.

---

### Agent 3 — Data Integration Squad
**Worktree:** `/Users/alexis/Documents/BNBHacks/.worktrees/agent3-data`
**Branch:** `agent/agent3-data`

**Implement:**
1. **Improvement #2 (ATR Regime Filter):** Add `_compute_atr_14(self, symbol: str) -> tuple[float | None, float | None]` that computes 14-period ATR from `self.price_cache.data[symbol]` OHLC points. Since cache only has single price values, compute ATR as the mean of `|high - low|` approximated by `|price[i] - price[i-1]|` over last 14 points. Add `_atr_regime(self, symbol: str) -> tuple[bool, float]` where `atr_ratio = atr_14 / atr_20_mean`. Return `(pass, atr_ratio)` where pass means `atr > atr_mean`. Modify `_evaluate_cheap_candidate` to call `_atr_regime` and add `atr_ratio` and `atr_pass` fields to `_CheapCandidate`. In `evaluate_token` and `evaluate_all`, skip tokens where `atr_pass is False` (unless ATR data is missing, then fail-open).
2. **Improvement #5 (Continuous Derivatives):** Add `_derivatives_component(self, funding_rate: float | None, oi_change: float | None) -> float`. When both are missing: return 0.5 (neutral, not a free pass). When present: `funding_score = clamp01(0.5 - funding_z * 0.5)` where `funding_z` is simplified to just `(funding_rate - 0.0) / 0.001` (normalized against 0.1% std). `oi_norm = clamp01(1.0 + oi_change / 100.0)`. Return `0.6 * funding_score + 0.4 * oi_norm`.
3. Add `self.funding_cache = LocalCache("funding_cache.json")` in `__init__`.
4. Modify `_entry_score` to call `_derivatives_component(candidate.funding_rate, candidate.open_interest_change)` instead of binary. NOTE: if this conflicts with Agent 2's changes to `_entry_score`, just implement the method and leave a clear comment in `_entry_score` for the orchestrator to wire.
5. Add CMC underutilized fields to `_evaluate_cheap_candidate`: capture `percent_change_7d`, `percent_change_30d`, `percent_change_90d`, `cmc_rank`, `watchlist_count` from `token_data` and store on `_CheapCandidate` for future use (telemetry only, no scoring impact yet).

**Validation:**
- `pytest tests/test_breakout_engine.py -v` must pass.
- Commit all changes.

---

### Agent 4 — Validation & Monitoring Squad
**Worktree:** `/Users/alexis/Documents/BNBHacks/.worktrees/agent4-tests`
**Branch:** `agent/agent4-tests`

**Implement:**
Add new tests to `tests/test_breakout_engine.py`:
1. `test_derivatives_neutral_on_missing_scores_0_5_not_1_0` — missing data yields 0.5, not 1.0.
2. `test_evaluate_token_uses_cached_momentum_z` — after `evaluate_all`, `evaluate_token` should use cached z-score.
3. `test_rsi_graded_not_binary` — RSI 70 > RSI 55; RSI 80 > 0 (not 0).
4. `test_quote_floor_above_threshold` — quote floor > threshold.
5. `test_atr_gate_blocks_low_volatility` — ATR below mean blocks entry.
6. `test_macro_stablecoin_slope_added` — stablecoin market cap increasing raises macro_score.
7. `test_dynamic_weights_sum_to_100` — `_regime_adjusted_weights` returns sum=100.
8. `test_continuous_derivatives_extreme_negative_funding` — funding = -0.05% yields high score (squeeze setup).

Also add a test fixture `_token_with_atr` that seeds `price_cache.data` with 20 price points so ATR calculation works.

**Validation:**
- `pytest tests/test_breakout_engine.py -v` must run (some tests may fail until all agent branches are merged; this is expected).
- Commit all changes.

---

### Agent 5 — Documentation & Deployment Squad
**Worktree:** `/Users/alexis/Documents/BNBHacks/.worktrees/agent5-docs`
**Branch:** `agent/agent5-docs`

**Implement:**
1. Write `docs/BREAKOUT_ENGINE_MIGRATION.md` with:
   - Summary of all 4 P0 bug fixes.
   - Summary of all 5 P1 improvements.
   - Configuration migration checklist (env var old → new values).
   - A/B testing protocol (shadow mode → 7 days → ramp).
   - Rollback plan (revert env vars).
2. Update `README.md` (or create `docs/BREAKOUT_ENGINE_SCORING.md`) documenting the new scoring formula with weights by regime.
3. Add `.env.example` comments (or create `docs/ENV_TEMPLATE.md`) showing all new env vars with descriptions.
4. Add deployment checklist with rollback steps.

**Validation:**
- Markdown files render correctly.
- Commit all changes.

## Merge Order
1. Agent 1 (Config & Bugs) — smallest footprint, safe base.
2. Agent 2 (Scoring Model) — big refactor on top of Agent 1.
3. Agent 3 (Data Integration) — additive on top of Agent 2.
4. Agent 4 (Tests) — independent, merged after code is stable.
5. Agent 5 (Docs) — independent, merged anytime after Agent 1.

## Final Verification
- `pytest tests/test_breakout_engine.py -v` all pass.
- `python -m py_compile src/strategy/6falgorithm/breakout_engine.py` succeeds.
- `python -m py_compile src/config/settings.py` succeeds.
- No import errors from `src.strategy.breakout_engine` shim.
