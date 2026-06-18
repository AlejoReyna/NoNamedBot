#!/usr/bin/env python3
"""Build merged feature matrix parquet from historical OHLCV and CMC snapshots.

Usage:
    python scripts/build_feature_matrix.py \
        --ohlcv-dir data/historical/binance \
        --output data/synthetic_labels.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import load_settings
from src.ml.feature_matrix import build_feature_matrix_from_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ohlcv-dir", default="data/historical/binance", help="Directory containing OHLCV parquet files")
    parser.add_argument("--output", default="data/synthetic_labels.parquet", help="Output parquet path")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (default: from settings)")
    parser.add_argument("--cmc-path", default="data/historical/cmc/premium_snapshots.parquet", help="CMC snapshots parquet")
    args = parser.parse_args()

    settings = load_settings()
    ohlcv_dir = Path(args.ohlcv_dir)
    cmc_path = Path(args.cmc_path) if args.cmc_path else None
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else settings.ml_universe_symbols

    matrix = build_feature_matrix_from_sources(
        ohlcv_dir=ohlcv_dir,
        cmc_path=cmc_path,
        symbols=symbols,
        execution_log_path=settings.execution_log_path,
    )
    matrix.to_parquet(out_path, index=False)
    attrs = getattr(matrix, "attrs", {})
    thresholds = attrs.get("label_thresholds", {})
    if thresholds:
        print(f"Label thresholds: {thresholds}")
    print(f"Wrote {len(matrix)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
