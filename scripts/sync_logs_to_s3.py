#!/usr/bin/env python3
"""Sync local logs to S3.

Usage:
    python scripts/sync_logs_to_s3.py
    python scripts/sync_logs_to_s3.py --dir logs --pattern "*.jsonl"
    python scripts/sync_logs_to_s3.py --dir logs/archive --pattern "*.gz"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.s3_logs_uploader import sync_directory
from src.config.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local logs to S3")
    parser.add_argument("--dir", default="logs", help="Local directory to sync")
    parser.add_argument("--pattern", default="*", help="File glob pattern")
    parser.add_argument(
        "--delete-after-upload",
        action="store_true",
        help="Delete local files after successful upload",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.s3_logs_enabled or not settings.s3_logs_bucket:
        print("S3 logs disabled or S3_LOGS_BUCKET not set; nothing to do.")
        return 0

    uploaded, failed = sync_directory(
        settings,
        args.dir,
        args.pattern,
        delete_after_upload=args.delete_after_upload,
    )
    print(f"Synced {uploaded} file(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
