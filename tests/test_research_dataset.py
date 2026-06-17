"""Tests for leakage-safe research dataset construction."""

from __future__ import annotations

import json

import pytest

from src.research.dataset import assert_no_leakage, build_dataset, feature_columns
from src.research.feature_contract import entry_feature_vector


def test_build_dataset_joins_entry_exit_and_excludes_exit_features(tmp_path: object) -> None:
    path = tmp_path / "trade_outcomes.jsonl"  # type: ignore[operator]
    rows = [
        {
            "event": "entry",
            "trade_id": "t1",
            "opened_at": 100.0,
            "symbol": "CAKE",
            "entry_price": 2.0,
            "entry_score": 55.0,
            "true_factor_count": 4,
            "factor_scores": {"volume_breakout": True, "rsi_in_range": False},
        },
        {
            "event": "exit",
            "trade_id": "t1",
            "closed_at": 200.0,
            "symbol": "CAKE",
            "exit_price": 2.2,
            "realized_pnl_usdc": 10.0,
            "realized_pnl_pct": 0.1,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    frame = build_dataset(path, tmp_path / "missing.sqlite")  # type: ignore[operator]
    features = feature_columns(frame)

    assert frame.iloc[0]["entry_win"] == 1
    assert frame.iloc[0]["factor_volume_breakout"] == 1
    assert "realized_pnl_usdc" not in features
    assert "closed_at" not in features
    # Whitelist: timestamps / prices / sizes / ids must never be model features.
    for excluded in ("opened_at", "entry_price", "size_usdc", "trade_id"):
        assert excluded not in features
    assert set(features) == {"factor_volume_breakout", "factor_rsi_in_range", "entry_score", "true_factor_count"}
    assert_no_leakage(features)


def test_assert_no_leakage_rejects_exit_fields() -> None:
    with pytest.raises(ValueError):
        assert_no_leakage(["entry_score", "closed_at"])


def test_assert_no_leakage_rejects_non_reproducible_fields() -> None:
    for bad in ("opened_at", "entry_price", "size_usdc"):
        with pytest.raises(ValueError):
            assert_no_leakage(["entry_score", bad])


def test_feature_columns_match_serving_feature_contract(tmp_path: object) -> None:
    """Trained feature names must be a subset of what the live builder produces."""

    path = tmp_path / "trade_outcomes.jsonl"  # type: ignore[operator]
    rows = [
        {
            "event": "entry",
            "trade_id": "t1",
            "opened_at": 100.0,
            "symbol": "CAKE",
            "entry_price": 2.0,
            "size_usdc": 50.0,
            "entry_score": 55.0,
            "true_factor_count": 3,
            "factor_scores": {"volume_breakout": True, "rsi_in_range": False},
        },
        {
            "event": "exit",
            "trade_id": "t1",
            "closed_at": 200.0,
            "symbol": "CAKE",
            "exit_price": 2.2,
            "realized_pnl_usdc": 10.0,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    frame = build_dataset(path, tmp_path / "missing.sqlite")  # type: ignore[operator]
    trained_features = set(feature_columns(frame))

    serving_features = set(
        entry_feature_vector(
            factor_scores={"volume_breakout": True, "rsi_in_range": False},
            entry_score=55.0,
            true_factor_count=3,
        )
    )
    # Every feature the model trains on must be reproducible at serving time.
    assert trained_features <= serving_features
