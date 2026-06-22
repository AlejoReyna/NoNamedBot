"""Tests for S3 log uploader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.common.s3_logs_uploader import upload_file
from src.config.settings import Settings


def test_upload_file_skips_when_disabled(tmp_path: Path) -> None:
    settings = Settings(
        s3_logs_enabled=False,
        s3_logs_bucket="my-bucket",
        s3_logs_region="us-east-1",
    )
    path = tmp_path / "test.log"
    path.write_text("hello")
    assert upload_file(settings, path) is False


def test_upload_file_skips_when_bucket_missing(tmp_path: Path) -> None:
    settings = Settings(
        s3_logs_enabled=True,
        s3_logs_bucket="",
        s3_logs_region="us-east-1",
    )
    path = tmp_path / "test.log"
    path.write_text("hello")
    assert upload_file(settings, path) is False


def test_upload_file_uses_boto3_client(tmp_path: Path) -> None:
    settings = Settings(
        s3_logs_enabled=True,
        s3_logs_bucket="my-bucket",
        s3_logs_region="us-east-1",
    )
    path = tmp_path / "test.log"
    path.write_text("hello")

    mock_client = MagicMock()
    with patch(
        "src.common.s3_logs_uploader._s3_client",
        return_value=mock_client,
    ):
        assert upload_file(settings, path, "my/key") is True
        mock_client.upload_file.assert_called_once()
        call_args = mock_client.upload_file.call_args
        assert call_args[0][1] == "my-bucket"
        assert call_args[0][2] == "my/key"


def test_upload_file_missing_file(tmp_path: Path) -> None:
    settings = Settings(
        s3_logs_enabled=True,
        s3_logs_bucket="my-bucket",
        s3_logs_region="us-east-1",
    )
    assert upload_file(settings, tmp_path / "missing.log") is False
