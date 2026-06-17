"""End-to-end smoke test for the entry-quality training pipeline.

Builds a tiny synthetic trade_outcomes log with signal, runs the full
train -> artifact -> model-card flow, and confirms the train/serve feature
contract matches (the bug class that produced an all-zeros shadow predictor).
LightGBM is optional; the LogReg baseline always runs.
"""

from __future__ import annotations

import json
import random

from src.research.feature_contract import entry_feature_vector
from src.research.train import train_entry_quality_model
from src.ml.model_store import load_artifact


def _write_synthetic_outcomes(path, n: int = 60, seed: int = 11) -> None:
    rng = random.Random(seed)
    rows = []
    ts = 1_781_000_000.0
    for i in range(n):
        tid = f"t{i:04d}"
        vol, six, rsi = rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.5
        win = rng.random() < (0.25 + 0.18 * (vol + six + rsi))  # signal: more factors -> higher win odds
        pnl = round(rng.uniform(0.5, 2.5) if win else -rng.uniform(0.5, 2.5), 4)
        ts += 300
        rows.append({
            "event": "entry", "trade_id": tid, "ts": ts, "opened_at": ts, "symbol": "CAKE",
            "entry_price": 2.0, "size_usdc": 2.0,
            "entry_score": round(40 + 10 * (vol + six + rsi), 2),
            "true_factor_count": int(vol + six + rsi),
            "factor_scores": {
                "volume_breakout": vol, "six_hour_high_break": six, "rsi_in_range": rsi,
                "regime_not_risk_off": True, "slippage_under_cap": True, "derivatives_risk_clear": True,
            },
        })
        ts += 600
        rows.append({
            "event": "exit", "trade_id": tid, "ts": ts, "closed_at": ts, "symbol": "CAKE",
            "entry_price": 2.0, "exit_price": 2.0 * (1 + pnl / 100),
            "realized_pnl_usdc": pnl, "realized_pnl_pct": pnl / 100,
            "exit_reason": "take_profit" if win else "stop_loss",
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_training_pipeline_produces_artifact_and_card(tmp_path) -> None:
    outcomes = tmp_path / "trade_outcomes.jsonl"
    _write_synthetic_outcomes(outcomes)
    out = tmp_path / "models" / "entry_quality_v1.pkl"

    card = train_entry_quality_model(
        trade_outcomes_path=outcomes,
        cmc_db_path=tmp_path / "missing.db",  # no CMC db -> exercises the no-enrichment path
        output_path=out,
        min_selected_trades=5,
        min_training_rows=20,
    )

    assert out.exists()
    assert out.with_suffix(".model_card.json").exists()
    assert set(card["promotion_gate"]["checks"]) == {
        "beats_rule_pnl", "auc_above_chance", "enough_selected_trades", "enough_training_rows",
    }
    assert card["metrics"]["training_rows"] >= 20


def test_trained_features_are_reproducible_at_serve_time(tmp_path) -> None:
    """Every trained feature must be buildable from a live candidate (no skew)."""
    outcomes = tmp_path / "trade_outcomes.jsonl"
    _write_synthetic_outcomes(outcomes)
    out = tmp_path / "models" / "entry_quality_v1.pkl"
    train_entry_quality_model(
        trade_outcomes_path=outcomes, cmc_db_path=tmp_path / "missing.db",
        output_path=out, min_selected_trades=5, min_training_rows=20,
    )

    artifact = load_artifact(out)
    serving = entry_feature_vector(
        factor_scores={"volume_breakout": True, "six_hour_high_break": False, "rsi_in_range": True,
                       "regime_not_risk_off": True, "slippage_under_cap": True, "derivatives_risk_clear": True},
        entry_score=66.0, true_factor_count=2,
    )
    assert set(artifact.feature_names) <= set(serving)


def test_gate_blocks_when_too_few_trades(tmp_path) -> None:
    """A high selection floor must fail the gate even on a profitable model."""
    outcomes = tmp_path / "trade_outcomes.jsonl"
    _write_synthetic_outcomes(outcomes)
    out = tmp_path / "models" / "entry_quality_v1.pkl"
    card = train_entry_quality_model(
        trade_outcomes_path=outcomes, cmc_db_path=tmp_path / "missing.db",
        output_path=out, min_selected_trades=10_000, min_training_rows=20,
    )
    assert card["promotion_gate"]["checks"]["enough_selected_trades"] is False
    assert card["promotion_gate"]["passed"] is False
