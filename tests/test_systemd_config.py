"""Tests for systemd service configuration."""

from __future__ import annotations

from pathlib import Path


def test_systemd_service_points_to_main_and_env() -> None:
    service = Path("systemd/cascade-ai.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/home/ec2-user/cascade-ai/.env" in service
    assert "python -m src.main --live" in service
    assert "WorkingDirectory=/home/ec2-user/cascade-ai" in service
    assert "RestartSec=30" in service
    assert "StartLimitBurst=10" in service
    assert "SyslogIdentifier=cascade-ai" in service
