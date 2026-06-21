"""Tests for environment-backed settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config.settings import Settings, load_settings


def test_load_settings_reads_opbnb_provider_url(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPBNB_PROVIDER_URL=https://opbnb.example\n", encoding="utf-8")
    monkeypatch.delenv("OPBNB_PROVIDER_URL", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.opbnb_provider_url == "https://opbnb.example"


def test_load_settings_defaults_opbnb_provider_url(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("OPBNB_PROVIDER_URL", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.opbnb_provider_url == "https://opbnb-mainnet-rpc.bnbchain.org"


def test_load_settings_reads_execution_log_path(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXECUTION_LOG_PATH=/tmp/cascade-execution.jsonl\n", encoding="utf-8")
    monkeypatch.delenv("EXECUTION_LOG_PATH", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.execution_log_path == "/tmp/cascade-execution.jsonl"


def test_load_settings_reads_decision_log_path(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DECISION_LOG_PATH=/tmp/cascade-decisions.jsonl\n", encoding="utf-8")
    monkeypatch.delenv("DECISION_LOG_PATH", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.decision_log_path == "/tmp/cascade-decisions.jsonl"


def test_load_settings_reads_demo_mode(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DEMO_MODE=true\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_MODE", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.demo_mode is True


def test_load_settings_reads_cmc_snapshot_ttl_seconds(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("CMC_SNAPSHOT_TTL_SECONDS=3600\n", encoding="utf-8")
    monkeypatch.delenv("CMC_SNAPSHOT_TTL_SECONDS", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.cmc_snapshot_ttl_seconds == 3600


def test_load_settings_reads_swap_approval_retry_knobs(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SWAP_APPROVAL_RETRY_MAX=2\n"
        "SWAP_APPROVAL_RETRY_DELAY_SECONDS=0.25\n"
        "SWAP_APPROVAL_SPENDER_ADDRESS=0x1111111111111111111111111111111111111111\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SWAP_APPROVAL_RETRY_MAX", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("SWAP_APPROVAL_RETRY_DELAY_SECONDS", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("SWAP_APPROVAL_SPENDER_ADDRESS", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.swap_approval_retry_max == 2
    assert settings.swap_approval_retry_delay_seconds == 0.25
    assert settings.swap_approval_spender_address == "0x1111111111111111111111111111111111111111"


def test_load_settings_defaults_cmc_snapshot_ttl_to_four_hours(monkeypatch: object, tmp_path: Path) -> None:
    # Flat heartbeat TTL for the paid x402 layer; event triggers (hot
    # candidates, real positions) refresh sooner when it matters.
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("CMC_SNAPSHOT_TTL_SECONDS", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.cmc_snapshot_ttl_seconds == 14400


def test_load_settings_does_not_expose_cmc_ephemeral_key(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("CMC_X402_EPHEMERAL_KEY=0xabc\n", encoding="utf-8")
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert not hasattr(settings, "cmc_x402_ephemeral_key")
    assert not hasattr(settings, "cmc_x402_private_key")


def test_load_settings_allows_keyless_primary_without_api_key(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("USE_KEYLESS_PRIMARY=true\n", encoding="utf-8")
    monkeypatch.delenv("CMC_API_KEY", raising=False)
    monkeypatch.delenv("USE_KEYLESS_PRIMARY", raising=False)
    monkeypatch.delenv("USE_DUAL_MARKET_DATA", raising=False)
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)

    settings = load_settings(str(env_path))

    assert settings.use_keyless_primary is True
    assert settings.cmc_api_key is None


def test_load_settings_auto_enables_dual_with_x402_signer(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("CMC_X402_EPHEMERAL_KEY=0xabc\n", encoding="utf-8")
    monkeypatch.delenv("USE_DUAL_MARKET_DATA", raising=False)
    monkeypatch.delenv("USE_KEYLESS_PRIMARY", raising=False)
    monkeypatch.delenv("CMC_X402_EPHEMERAL_KEY", raising=False)
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)

    settings = load_settings(str(env_path))

    assert settings.use_dual_market_data is True
    assert settings.use_keyless_primary is False


def test_load_settings_respects_explicit_dual_disable(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CMC_X402_EPHEMERAL_KEY=0xabc\nUSE_DUAL_MARKET_DATA=false\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("USE_DUAL_MARKET_DATA", raising=False)

    settings = load_settings(str(env_path))

    assert settings.use_dual_market_data is False


def test_load_settings_reads_scored_breakout_knobs(monkeypatch: object, tmp_path: Path) -> None:
    for name in (
        "BREAKOUT_REFERENCE_WINDOWS_HOURS",
        "BREAKOUT_ENTRY_SCORE_MIN",
        "BREAKOUT_QUOTE_SCORE_BUFFER",
        "BREAKOUT_NEAR_MISS_COOLDOWN_CYCLES",
        "MAX_CHASE_PCT",
        "TRAIL_STEP1_PROFIT_PCT",
        "TRAIL_STEP2_STOP_PCT",
    ):
        monkeypatch.delenv(name, raising=False)  # type: ignore[attr-defined]
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "BREAKOUT_REFERENCE_WINDOWS_HOURS=3,6,24",
                "BREAKOUT_ENTRY_SCORE_MIN=47",
                "BREAKOUT_QUOTE_SCORE_BUFFER=4",
                "BREAKOUT_NEAR_MISS_COOLDOWN_CYCLES=2",
                "MAX_CHASE_PCT=0.03",
                "TRAIL_STEP1_PROFIT_PCT=0.09",
                "TRAIL_STEP2_STOP_PCT=0.025",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(str(env_path))

    assert settings.breakout_reference_windows_hours == [3, 6, 24]
    assert settings.breakout_entry_score_min == 47
    assert settings.breakout_quote_score_buffer == 4
    assert settings.breakout_near_miss_cooldown_cycles == 2
    assert settings.max_chase_pct == 0.03
    assert settings.trail_step1_profit_pct == 0.09
    assert settings.trail_step2_stop_pct == 0.025


def test_min_entry_factors_is_bounded_to_core_factor_count() -> None:
    assert Settings(min_entry_factors=4).min_entry_factors == 4

    with pytest.raises(ValidationError):
        Settings(min_entry_factors=5)


def test_load_settings_reads_model_shadow_flags(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENABLE_MODEL_SHADOW=true\n"
        "MODEL_SHADOW_PATH=models/custom.pkl\n"
        "MODEL_SHADOW_THRESHOLD=0.61\n"
        "ML_UNIVERSE_SYMBOLS=bnb,cake\n",
        encoding="utf-8",
    )
    for name in ("ENABLE_MODEL_SHADOW", "MODEL_SHADOW_PATH", "MODEL_SHADOW_THRESHOLD", "ML_UNIVERSE_SYMBOLS"):
        monkeypatch.delenv(name, raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.enable_model_shadow is True
    assert settings.model_shadow_path == "models/custom.pkl"
    assert settings.model_shadow_threshold == 0.61
    assert settings.ml_universe_symbols == ["BNB", "CAKE"]
