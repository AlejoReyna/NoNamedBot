"""Rule-based market chat replies for the x402 terminal UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _tail_jsonl(path: Path, n: int = 5) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-n:]


def _read_cmc_cache(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {}
    try:
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        out: dict[str, Any] = {}
        try:
            row = conn.execute(
                "SELECT value, classification, timestamp FROM fear_greed ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                out["fear_greed"] = dict(row)
            row = conn.execute(
                "SELECT btc_dominance, altcoin_volume, timestamp FROM market_metrics ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                out["market_metrics"] = dict(row)
            rows = conn.execute(
                "SELECT token, funding_rate, open_interest, timestamp FROM funding_rates ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
            if rows:
                out["funding_rates"] = [dict(r) for r in rows]
        finally:
            conn.close()
        return out
    except Exception:
        return {}


def build_chat_reply(
    message: str,
    *,
    health_snapshot: dict[str, Any] | None = None,
    decision_log_path: str | Path = "decision_log.jsonl",
    cmc_db_path: str | Path = "data/cmc_premium.db",
) -> dict[str, str]:
    """Return a chat reply dict with ``reply`` and ``source`` keys."""

    text = (message or "").strip().lower()
    health = health_snapshot or {}
    cmc = _read_cmc_cache(Path(cmc_db_path))
    decisions = _tail_jsonl(Path(decision_log_path), 3)

    if any(k in text for k in ("health", "status", "bot", "running")):
        lines = [
            f"Bot status: {health.get('status', 'unknown')}",
            f"Open positions: {health.get('positions', 0)}",
            f"Daily trades: {health.get('daily_trades', 0)}",
            f"Drawdown: {health.get('drawdown_pct', 0)}%",
        ]
        if health.get("last_cycle"):
            lines.append(f"Last cycle: {health['last_cycle']}")
        return {"reply": "\n".join(lines), "source": "health snapshot"}

    if any(k in text for k in ("fear", "greed", "sentiment")):
        fg = cmc.get("fear_greed")
        if fg:
            return {
                "reply": f"Fear & Greed: {fg.get('value')} ({fg.get('classification')}) as of {fg.get('timestamp')}",
                "source": "CMC x402 cache · fear_greed",
            }
        return {
            "reply": "No fear & greed data cached yet. Run scripts/cmc_feature_collector.py to populate x402 premium data.",
            "source": "cmc_premium.db",
        }

    if any(k in text for k in ("funding", "open interest", "derivatives")):
        rates = cmc.get("funding_rates") or []
        if rates:
            lines = [
                f"{r.get('token')}: funding={r.get('funding_rate')} OI={r.get('open_interest')} @ {r.get('timestamp')}"
                for r in rates
            ]
            return {"reply": "\n".join(lines), "source": "CMC x402 cache · funding_rates"}
        return {
            "reply": "No derivatives metrics cached yet. Collector cron fills data/cmc_premium.db every 15 minutes.",
            "source": "cmc_premium.db",
        }

    if any(k in text for k in ("dominance", "btc", "altcoin", "market cap")):
        mm = cmc.get("market_metrics")
        if mm:
            return {
                "reply": (
                    f"BTC dominance: {mm.get('btc_dominance')}%\n"
                    f"Altcoin volume: {mm.get('altcoin_volume')}\n"
                    f"As of {mm.get('timestamp')}"
                ),
                "source": "CMC x402 cache · market_metrics",
            }
        return {
            "reply": "Market metrics not cached yet. Ask again after the CMC collector runs.",
            "source": "cmc_premium.db",
        }

    if any(k in text for k in ("decision", "trade", "enter", "last", "signal")):
        if not decisions:
            return {"reply": "No recent decisions in decision_log.jsonl.", "source": decision_log_path}
        lines = []
        for row in decisions:
            lines.append(
                f"{row.get('timestamp', '?')}: {row.get('action', 'WAIT')} "
                f"{row.get('symbol', '-')} — {str(row.get('reason', ''))[:60]}"
            )
        return {"reply": "\n".join(lines), "source": str(decision_log_path)}

    return {
        "reply": (
            "I can answer questions about:\n"
            "• Bot health & positions\n"
            "• Fear & greed (x402)\n"
            "• Funding rates & open interest\n"
            "• BTC dominance / market metrics\n"
            "• Latest trading decisions\n\n"
            "Try: \"What's the fear and greed index?\" or \"Show last decisions\""
        ),
        "source": "market terminal help",
    }
