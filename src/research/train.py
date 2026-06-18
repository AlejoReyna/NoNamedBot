"""Train entry-quality models with walk-forward validation."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.ml.model_store import ModelArtifact, save_artifact
from src.research.dataset import assert_no_leakage, build_dataset, feature_columns


@dataclass(frozen=True)
class EvaluationResult:
    model_name: str
    folds: int
    mean_auc: float
    mean_accuracy: float
    rule_pnl_usdc: float
    model_pnl_usdc: float
    selected_trades: int


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _candidate_models() -> dict[str, Any]:
    models: dict[str, Any] = {
        "logreg": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
        )
    }
    try:
        from lightgbm import LGBMClassifier

        models["lgb"] = LGBMClassifier(
            objective="binary",
            n_estimators=250,
            learning_rate=0.03,
            num_leaves=31,
            min_child_samples=10,
            n_jobs=2,
            random_state=42,
            verbose=-1,
        )
    except ImportError:
        pass
    return models


def _predict_probability(model: Any, x_test: Any) -> list[float]:
    if hasattr(model, "predict_proba"):
        return [float(value) for value in model.predict_proba(x_test)[:, 1]]
    preds = model.predict(x_test)
    return [float(value) for value in preds]


def evaluate_walk_forward(
    frame: pd.DataFrame,
    model: Any,
    features: list[str],
    *,
    threshold: float = 0.55,
    splits: int = 3,
    skip_leakage_check: bool = False,
) -> EvaluationResult:
    """Evaluate model against the rule baseline on held-out time slices."""

    if not skip_leakage_check:
        assert_no_leakage(features)
    # Fit/predict on positional arrays (not named DataFrames) so the estimator is
    # feature-name-agnostic and matches exactly how the live ModelPredictor scores
    # it (an ordered list keyed by artifact.feature_names). This keeps train and
    # serve identical and avoids sklearn's feature-name mismatch warning.
    x = frame[features].fillna(0.0).to_numpy()
    y = frame["entry_win"].astype(int)
    pnl = pd.to_numeric(frame["realized_pnl_usdc"], errors="coerce").fillna(0.0)
    max_splits = max(2, min(splits, len(frame) - 1))
    splitter = TimeSeriesSplit(n_splits=max_splits)
    aucs: list[float] = []
    accuracies: list[float] = []
    rule_pnl = 0.0
    model_pnl = 0.0
    selected = 0

    for train_idx, test_idx in splitter.split(x):
        y_train = y.iloc[train_idx]
        y_test = y.iloc[test_idx]
        if y_train.nunique() < 2:
            continue
        fitted = model.fit(x[train_idx], y_train)
        probabilities = _predict_probability(fitted, x[test_idx])
        predictions = [1 if prob >= threshold else 0 for prob in probabilities]
        if y_test.nunique() >= 2:
            aucs.append(float(roc_auc_score(y_test, probabilities)))
        accuracies.append(float(accuracy_score(y_test, predictions)))
        fold_pnl = pnl.iloc[test_idx].reset_index(drop=True)
        rule_pnl += float(fold_pnl.sum())
        for pred, trade_pnl in zip(predictions, fold_pnl):
            if pred:
                selected += 1
                model_pnl += float(trade_pnl)

    return EvaluationResult(
        model_name=type(model).__name__,
        folds=len(accuracies),
        mean_auc=float(sum(aucs) / len(aucs)) if aucs else 0.0,
        mean_accuracy=float(sum(accuracies) / len(accuracies)) if accuracies else 0.0,
        rule_pnl_usdc=rule_pnl,
        model_pnl_usdc=model_pnl,
        selected_trades=selected,
    )


def _build_promotion_gate(
    metrics: dict[str, float], min_selected_trades: int, min_training_rows: int
) -> dict[str, Any]:
    """Pass only when the model beats the rule baseline AND traded enough to mean it."""

    checks = {
        "beats_rule_pnl": metrics["pnl_delta_usdc"] > 0.0,
        "auc_above_chance": metrics["mean_auc"] >= 0.5,
        "enough_selected_trades": metrics["selected_trades"] >= min_selected_trades,
        "enough_training_rows": metrics["training_rows"] >= min_training_rows,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "min_selected_trades": min_selected_trades,
            "min_training_rows": min_training_rows,
        },
        "rule_baseline": "all rule-engine entries in held-out walk-forward folds",
    }


def train_entry_quality_model(
    *,
    trade_outcomes_path: str | Path = "logs/trade_outcomes.jsonl",
    cmc_db_path: str | Path = "data/cmc_premium.db",
    feature_matrix_path: str | Path | None = None,
    output_path: str | Path = "models/entry_quality_v1.pkl",
    model_card_path: str | Path | None = None,
    threshold: float = 0.55,
    min_selected_trades: int = 10,
    min_training_rows: int = 30,
) -> dict[str, Any]:
    """Build the dataset, evaluate candidates, and export the best artifact.

    ``min_selected_trades`` / ``min_training_rows`` guard the promotion gate: a
    model that vetoes (almost) everything trivially shows ``model_pnl > rule_pnl``
    in a losing window by trading nothing, and a model trained on a handful of
    rows is noise. The gate only passes when the model actually selects enough
    trades and was trained on enough data — not just on a positive PnL delta.
    """

    if feature_matrix_path is not None and Path(feature_matrix_path).exists():
        frame = pd.read_parquet(feature_matrix_path)
        frame = frame.rename(columns={"label": "entry_win"})
        # Synthetic matrix has no realized_pnl; fill with dummy so downstream
        # math stays happy. The promotion gate will still fail because the model
        # does not beat the rule baseline (both are 0), which is expected for demo.
        if "realized_pnl_usdc" not in frame.columns:
            frame["realized_pnl_usdc"] = 0.0
        if "realized_pnl_pct" not in frame.columns:
            frame["realized_pnl_pct"] = 0.0
        if "opened_at" not in frame.columns:
            ts = frame.get("timestamp", pd.Series(range(len(frame))))
            if pd.api.types.is_datetime64_any_dtype(ts):
                ts = ts.astype("int64") / 1e9
            frame["opened_at"] = ts.astype(float)
        # Use all numeric columns as features (demo / synthetic-label path)
        features = [
            col for col in frame.columns
            if pd.api.types.is_numeric_dtype(frame[col])
            and col not in {"label", "entry_win", "realized_pnl_usdc", "realized_pnl_pct", "opened_at", "ts", "timestamp"}
        ]
        skip_leakage_check = True
    else:
        frame = build_dataset(trade_outcomes_path, cmc_db_path)
        features = feature_columns(frame)
        skip_leakage_check = False
    if frame.empty:
        raise ValueError("no closed trades or feature matrix rows available for training")
    if not features:
        raise ValueError("no numeric entry-time features available")
    if frame["entry_win"].nunique() < 2:
        raise ValueError("entry_win has a single class; collect more closed trades before training")

    models = _candidate_models()
    evaluations: dict[str, EvaluationResult] = {}
    for name, model in models.items():
        evaluations[name] = evaluate_walk_forward(frame, model, features, threshold=threshold, skip_leakage_check=skip_leakage_check)

    best_name = max(
        evaluations,
        key=lambda name: (evaluations[name].model_pnl_usdc - evaluations[name].rule_pnl_usdc, evaluations[name].mean_auc),
    )
    final_model = models[best_name].fit(
        frame[features].fillna(0.0).to_numpy(), frame["entry_win"].astype(int)
    )
    best = evaluations[best_name]
    metrics = {
        "mean_auc": best.mean_auc,
        "mean_accuracy": best.mean_accuracy,
        "rule_pnl_usdc": best.rule_pnl_usdc,
        "model_pnl_usdc": best.model_pnl_usdc,
        "pnl_delta_usdc": best.model_pnl_usdc - best.rule_pnl_usdc,
        "selected_trades": float(best.selected_trades),
        "training_rows": float(len(frame)),
    }

    artifact = ModelArtifact(
        model=final_model,
        feature_names=features,
        version="entry_quality_v1",
        trained_at=datetime.now(timezone.utc).isoformat(),
        metrics=metrics,
        model_type=best_name,
    )
    save_artifact(output_path, artifact)

    card = {
        "version": artifact.version,
        "trained_at": artifact.trained_at,
        "git_sha": _git_sha(),
        "target": "entry_win_after_fees",
        "threshold": threshold,
        "features": features,
        "metrics": metrics,
        "all_evaluations": {name: result.__dict__ for name, result in evaluations.items()},
        "promotion_gate": _build_promotion_gate(metrics, min_selected_trades, min_training_rows),
    }
    card_path = Path(model_card_path) if model_card_path else Path(output_path).with_suffix(".model_card.json")
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2, sort_keys=True), encoding="utf-8")
    return card


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-outcomes", default="logs/trade_outcomes.jsonl")
    parser.add_argument("--cmc-db", default="data/cmc_premium.db")
    parser.add_argument("--feature-matrix", default="")
    parser.add_argument("--output", default="models/entry_quality_v1.pkl")
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--min-selected-trades", type=int, default=10)
    parser.add_argument("--min-training-rows", type=int, default=30)
    args = parser.parse_args()
    card = train_entry_quality_model(
        trade_outcomes_path=args.trade_outcomes,
        cmc_db_path=args.cmc_db,
        feature_matrix_path=args.feature_matrix or None,
        output_path=args.output,
        threshold=args.threshold,
        min_selected_trades=args.min_selected_trades,
        min_training_rows=args.min_training_rows,
    )
    print(json.dumps({"output": args.output, "metrics": card["metrics"], "gate": card["promotion_gate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
