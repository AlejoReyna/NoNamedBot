"""Offline replay of live and shadow JSONL decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def replay_decisions(
    live_log_path: str = "logs/decision_live.jsonl",
    shadow_log_path: str = "logs/decision_shadow.jsonl",
    sentiment_live_path: str = "logs/sentiment_live.jsonl",
    sentiment_shadow_path: str = "logs/sentiment_shadow.jsonl",
    output_path: str = "artifacts/replay_report.json",
) -> dict[str, Any]:
    """Correlate live and shadow decisions by cycle_id and write a JSON report."""

    live = _read_jsonl(live_log_path)
    shadow = _read_jsonl(shadow_log_path)
    sentiment_live = _read_jsonl(sentiment_live_path)
    sentiment_shadow = _read_jsonl(sentiment_shadow_path)
    shadow_by_cycle = _group_by_cycle(shadow)
    matched_cycles = [item for item in live if _cycle_id(item) in shadow_by_cycle]
    report = {
        "cycles_live": len(live),
        "cycles_shadow": len(shadow_by_cycle),
        "matched_cycles": len(matched_cycles),
        "shadow_signals_generated": sum(1 for item in shadow if item.get("hypothetical_action") == "ENTER"),
        "live_entries": sum(1 for item in live if item.get("action") == "ENTER"),
        "sentiment_live_records": len(sentiment_live),
        "sentiment_shadow_records": len(sentiment_shadow),
        "comparisons": [
            {
                "cycle_id": _cycle_id(item),
                "live_action": item.get("action"),
                "shadow_variants": len(shadow_by_cycle.get(_cycle_id(item), [])),
            }
            for item in matched_cycles
        ],
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _group_by_cycle(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_cycle_id(record), []).append(record)
    return grouped


def _cycle_id(record: dict[str, Any]) -> int:
    return int(record.get("cycle_id", record.get("cycle_number", 0)) or 0)
