"""S3 offloading helper for structured and legacy logs.

Reads credentials from environment variables or IAM role / instance profile.
Never blocks trading: upload failures are logged and skipped.
"""

from __future__ import annotations

import gzip
import logging
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def _make_boto3_session(settings: Any) -> Any:
    """Return a boto3 session configured from settings or env/IAM."""

    try:
        import boto3  # type: ignore
    except ImportError as exc:
        LOGGER.warning("boto3 not installed; S3 uploads disabled: %s", exc)
        return None

    kwargs: dict[str, Any] = {"region_name": settings.s3_logs_region}
    if settings.s3_logs_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_logs_endpoint_url

    credentials: dict[str, str | None] = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "aws_session_token": settings.aws_session_token,
    }
    if all(credentials.values()):
        kwargs.update({k: v for k, v in credentials.items() if v})

    return boto3.Session(**kwargs)


def _s3_client(settings: Any) -> Any | None:
    session = _make_boto3_session(settings)
    if session is None:
        return None
    try:
        return session.client("s3")
    except Exception as exc:
        LOGGER.warning("Failed to create S3 client: %s", exc)
        return None


def _default_s3_key(local_path: Path, prefix: str = "logs") -> str:
    """Build an S3 key like logs/{hostname}/{date}/{relative-or-filename}."""

    hostname = socket.gethostname().split(".")[0]
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    name = local_path.name
    return f"{prefix}/{hostname}/{today}/{name}"


def upload_file(
    settings: Any,
    local_path: str | Path,
    s3_key: str | None = None,
    *,
    extra_args: dict[str, Any] | None = None,
) -> bool:
    """Upload a single file to S3. Returns True on success."""

    if not settings.s3_logs_enabled or not settings.s3_logs_bucket:
        return False

    path = Path(local_path)
    if not path.exists():
        LOGGER.warning("S3 upload skipped; file not found: %s", path)
        return False

    key = s3_key or _default_s3_key(path, settings.s3_logs_prefix)
    client = _s3_client(settings)
    if client is None:
        return False

    args = extra_args or {}
    if str(path).endswith(".gz"):
        args.setdefault("ContentEncoding", "gzip")
        args.setdefault("ContentType", "application/gzip")
    else:
        args.setdefault("ContentType", "application/json")

    try:
        client.upload_file(str(path), settings.s3_logs_bucket, key, ExtraArgs=args)
        LOGGER.info("Uploaded %s -> s3://%s/%s", path, settings.s3_logs_bucket, key)
        return True
    except Exception as exc:
        LOGGER.warning("S3 upload failed for %s: %s", path, exc)
        return False


def sync_directory(
    settings: Any,
    local_dir: str | Path,
    pattern: str = "*",
    *,
    delete_after_upload: bool = False,
) -> tuple[int, int]:
    """Upload all matching files in a directory. Returns (uploaded, failed)."""

    if not settings.s3_logs_enabled or not settings.s3_logs_bucket:
        return 0, 0

    directory = Path(local_dir)
    if not directory.exists():
        return 0, 0

    uploaded = 0
    failed = 0
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        key = _default_s3_key(path, settings.s3_logs_prefix)
        if upload_file(settings, path, key):
            uploaded += 1
            if delete_after_upload:
                try:
                    path.unlink()
                except Exception as exc:
                    LOGGER.warning("Could not delete %s after upload: %s", path, exc)
        else:
            failed += 1
    return uploaded, failed


def upload_rotated_archive(settings: Any, archive_path: str | Path) -> bool:
    """Upload a rotated log archive and optionally prune local copy on success."""

    path = Path(archive_path)
    if not path.exists():
        return False

    key = _default_s3_key(path, settings.s3_logs_prefix)
    return upload_file(settings, path, key)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.config.settings import load_settings

    logging.basicConfig(level=logging.INFO)
    s = load_settings()
    if s.s3_logs_enabled and s.s3_logs_bucket:
        upload_file(s, "logs/bot.log")
    else:
        print("S3 logs disabled or bucket not configured")
