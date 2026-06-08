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


def test_load_settings_defaults_cmc_snapshot_ttl_to_two_hours(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("CMC_SNAPSHOT_TTL_SECONDS", raising=False)  # type: ignore[attr-defined]

    settings = load_settings(str(env_path))

    assert settings.cmc_snapshot_ttl_seconds == 7200


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

    settings = load_settings(str(env_path))

    assert settings.use_keyless_primary is True
    assert settings.cmc_api_key is None


def test_load_settings_auto_enables_dual_with_x402_signer(monkeypatch: object, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("CMC_X402_EPHEMERAL_KEY=0xabc\n", encoding="utf-8")
    monkeypatch.delenv("USE_DUAL_MARKET_DATA", raising=False)
    monkeypatch.delenv("USE_KEYLESS_PRIMARY", raising=False)

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


def test_min_entry_factors_is_bounded_to_core_factor_count() -> None:
    assert Settings(min_entry_factors=4).min_entry_factors == 4

    with pytest.raises(ValidationError):
        Settings(min_entry_factors=5)
