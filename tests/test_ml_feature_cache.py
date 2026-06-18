"""Tests for MLFeatureCache CMC metrics history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.data.ml_feature_cache import MLFeatureCache


def test_cmc_metrics_prior_and_funding_history(tmp_path) -> None:
    cache = MLFeatureCache(tmp_path / "ml_cache.sqlite")
    now = datetime.now(timezone.utc)
    prior_ts = now - timedelta(hours=25)
    mid_ts = now - timedelta(hours=12)

    cache.record_cmc_metrics(
        {
            "CAKE": {"fear_greed_index": 40, "funding_rate": 0.0001},
        },
        timestamp=prior_ts,
    )
    cache.record_cmc_metrics(
        {
            "CAKE": {"fear_greed_index": 55, "funding_rate": 0.0002},
        },
        timestamp=mid_ts,
    )
    cache.record_cmc_metrics(
        {
            "CAKE": {"fear_greed_index": 60, "funding_rate": 0.0003},
        },
        timestamp=now,
    )

    prior = cache.get_fear_greed_prior(hours_ago=24.0)
    assert prior is not None
    assert abs(prior - 0.40) < 0.01

    history = cache.get_funding_history("CAKE", days=7.0)
    assert len(history) == 3
    assert history[-1] == 0.0003


def test_new_columns_recorded_and_retrievable(tmp_path) -> None:
    cache = MLFeatureCache(tmp_path / "ml_cache.sqlite")
    now = datetime.now(timezone.utc)

    cache.record_cmc_metrics(
        {
            "CAKE": {
                "fear_greed_index": 55,
                "funding_rate": 0.0001,
                "social_dominance": 1.5,
                "social_volume_change_24h": 10.0,
                "market_cap_dominance": 0.25,
                "volume_change_24h": 5.0,
                "open_interest_change_pct": 2.0,
            },
        },
        timestamp=now,
    )

    assert cache.get_cmc_metric_history("CAKE", "social_dominance", days=1.0) == [1.5]
    assert cache.get_cmc_metric_history("CAKE", "social_volume_change_24h", days=1.0) == [10.0]
    assert cache.get_cmc_metric_history("CAKE", "market_cap_dominance", days=1.0) == [0.25]
    assert cache.get_cmc_metric_history("CAKE", "volume_change_24h", days=1.0) == [5.0]
    assert cache.get_cmc_metric_history("CAKE", "open_interest_change_pct", days=1.0) == [2.0]
    assert cache.get_cmc_metric_history("CAKE", "fear_greed_index", days=1.0) == [0.55]
    assert cache.get_cmc_metric_history("CAKE", "funding_rate", days=1.0) == [0.0001]


def test_get_fear_greed_with_delta(tmp_path) -> None:
    cache = MLFeatureCache(tmp_path / "ml_cache.sqlite")
    now = datetime.now(timezone.utc)
    prior_ts = now - timedelta(hours=25)

    cache.record_cmc_metrics(
        {"CAKE": {"fear_greed_index": 40}},
        timestamp=prior_ts,
    )
    cache.record_cmc_metrics(
        {"CAKE": {"fear_greed_index": 60}},
        timestamp=now,
    )

    prior, delta = cache.get_fear_greed_with_delta(hours_ago=24.0)
    assert prior is not None
    assert abs(prior - 0.40) < 0.01
    assert delta is not None
    assert abs(delta - 0.20) < 0.01


def test_get_fear_greed_with_delta_returns_none_when_missing(tmp_path) -> None:
    cache = MLFeatureCache(tmp_path / "ml_cache.sqlite")
    now = datetime.now(timezone.utc)

    cache.record_cmc_metrics(
        {"CAKE": {"fear_greed_index": 60}},
        timestamp=now,
    )

    prior, delta = cache.get_fear_greed_with_delta(hours_ago=24.0)
    assert prior is None
    assert delta is None


def test_db_migration_adds_columns(tmp_path) -> None:
    db_path = tmp_path / "old_cache.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE cmc_metrics (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            fear_greed_index REAL,
            funding_rate REAL,
            PRIMARY KEY (timestamp, symbol)
        )
        """
    )
    conn.commit()
    conn.close()

    cache = MLFeatureCache(db_path)
    now = datetime.now(timezone.utc)
    cache.record_cmc_metrics(
        {
            "CAKE": {
                "fear_greed_index": 55,
                "funding_rate": 0.0001,
                "social_dominance": 1.5,
                "social_volume_change_24h": 10.0,
                "market_cap_dominance": 0.25,
                "volume_change_24h": 5.0,
                "open_interest_change_pct": 2.0,
            },
        },
        timestamp=now,
    )

    assert cache.get_cmc_metric_history("CAKE", "social_dominance", days=1.0) == [1.5]
    assert cache.get_cmc_metric_history("CAKE", "volume_change_24h", days=1.0) == [5.0]
    assert cache.get_cmc_metric_history("CAKE", "open_interest_change_pct", days=1.0) == [2.0]


def test_get_cmc_metric_history_rejects_unknown_metric(tmp_path) -> None:
    cache = MLFeatureCache(tmp_path / "ml_cache.sqlite")
    assert cache.get_cmc_metric_history("CAKE", "not_a_metric", days=1.0) == []
