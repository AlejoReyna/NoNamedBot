# ML Training & Shadow Runbook

How to go from accumulated trade outcomes → a trained entry-quality model → live
shadow comparison → (only if it earns it) advisory influence. Training runs
**offline** (dev box or CI), never on the EC2 trading box.

---

## 0. Prerequisites

- **Enough closed trades.** `trade_outcomes.jsonl` must contain entry↔exit pairs
  (joined by `trade_id`) with **both** win and loss outcomes. Rough floor: ~30
  closed trades to train, more to trust. Check:
  ```bash
  python scripts/diagnose_trades.py   # "closed trades" count + win rate
  ```
- **ML deps** (offline only — keep them off the trading runtime):
  ```bash
  pip install -r requirements-ml.txt   # pandas, scikit-learn, lightgbm
  ```

---

## 1. Get the data to where you train

The trading box writes locally. Pull a copy to your dev/CI machine — do **not**
train on the EC2 box.

```bash
scp ec2-user@<box>:/home/ec2-user/cascade-ai/logs/trade_outcomes.jsonl ./logs/
scp ec2-user@<box>:/home/ec2-user/cascade-ai/data/cmc_premium.db        ./data/   # optional
```

(If/when you stand up S3 sync, pull from there instead.)

---

## 2. Train

```bash
python -m src.research.train \
  --trade-outcomes logs/trade_outcomes.jsonl \
  --cmc-db data/cmc_premium.db \
  --output models/entry_quality_v1.pkl \
  --min-selected-trades 10 \
  --min-training-rows 30
```

This walk-forward-evaluates a LogReg baseline and (if installed) LightGBM, picks
the best by PnL-vs-rule then AUC, and writes:

- `models/entry_quality_v1.pkl` — the artifact (model + feature names + metrics)
- `models/entry_quality_v1.model_card.json` — metrics, git SHA, **promotion gate**
- `models/entry_quality_v1.meta.json` — quick summary

---

## 3. Read the gate before doing anything else

Open the model card and look at `promotion_gate`:

```json
"promotion_gate": {
  "passed": true,
  "checks": {
    "beats_rule_pnl": true,          // model PnL > rule-engine PnL on held-out folds
    "auc_above_chance": true,        // mean_auc >= 0.5
    "enough_selected_trades": true,  // didn't "win" by trading nothing
    "enough_training_rows": true     // trained on enough data to mean something
  }
}
```

**If `passed` is false, stop.** Collect more trades or accept the model has no
edge. Do not deploy a model that can't beat the rule engine after fees — that's
the whole point of the gate.

---

## 4. Enable shadow (zero capital risk)

The model only *logs* alongside live decisions; it never touches trades.

```bash
# copy the artifact onto the box
scp models/entry_quality_v1.pkl ec2-user@<box>:/home/ec2-user/cascade-ai/models/

# on the box .env:
MODEL_SHADOW_PATH=models/entry_quality_v1.pkl
MODEL_SHADOW_THRESHOLD=0.55
ENABLE_MODEL_SHADOW=true

sudo systemctl restart cascade-ai
```

Serving needs the model object to unpickle, so the box needs
`requirements-ml` installed too — or export the model to a dependency-light
format. If the artifact can't load, `ModelPredictor` fail-closes (disabled,
neutral output) and you'll see `ModelPredictor disabled:` in the log.

---

## 5. Compare in shadow (2–4 weeks)

Each cycle with a candidate now writes a `trained_model` row to
`logs/decision_shadow.jsonl` next to the `jump_inspired` variant. Compare the
model's hypothetical calls against what the rule engine actually did and the
realized outcomes:

```bash
tail -f logs/decision_shadow.jsonl | grep trained_model
python scripts/replay_shadow.py        # / ab_test_runner.py
```

The shadow predictor is scored with the **same** feature builder as training
(`entry_feature_vector`), so what it sees live equals what it learned — no skew.
Large divergence between shadow PnL and the backtest = leakage or a stale model;
investigate before trusting it.

---

## 6. Promote to advisory (only if shadow wins)

Only after shadow consistently beats the rule engine after fees: wire the model
as an **advisory** input (confidence multiplier / veto on the breakout scorer or
sizing) — never the sole entry trigger. Guardrails keep veto power, and a
missing/stale artifact must auto-revert to pure rule-based behavior.

---

## Notes / gotchas

- **Features are the reproducible whitelist only:** `factor_*` flags +
  `entry_score` + `true_factor_count`. CMC quote columns are deliberately
  excluded (the live snapshot can't reproduce the collector-DB schema 1:1).
- **Retrain on a rolling window.** Markets are non-stationary; a model trained on
  last month decays. Re-run steps 1–3 periodically and keep the previous artifact
  for instant rollback.
- **Budget/runway:** shadow needs the box funded (gas + x402) so trades keep
  flowing and data keeps accumulating.
- **Entry quality today:** live entries fire on ~3/6 factors (the real-signal
  factors — volume/6h/RSI — often fail), so the rule baseline the model must beat
  is itself weak. The model's job is to learn which of those weak entries
  actually pay.
