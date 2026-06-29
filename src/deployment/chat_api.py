"""Kimi LLM-powered market chat backend with live context injection.

Replaces the legacy rule-based terminal with a Moonshot AI (Kimi) chat
backend while keeping the HTTP contract unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from openai import OpenAI

LOGGER = logging.getLogger(__name__)

# Module-level lazy client and session storage
_client: OpenAI | None = None
_sessions: dict[str, list[dict]] = {}
_session_lock = threading.Lock()
_MAX_SESSIONS = 100

_LEGACY_KEYWORDS = {
    "health", "status", "fear", "greed", "funding", "dominance",
    "decision", "trade", "x402", "payment",
}

_MISSING_KEY_MSG = "⚠️  KIMI_API_KEY is not set. AI chat is disabled.\n\n"


def _get_client() -> OpenAI | None:
    """Return a lazily-initialized OpenAI client for Kimi, or None if key is missing."""
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("KIMI_API_KEY")
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    if not api_key:
        return None
    _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def _tail_jsonl(path: Path, n: int = 5) -> list[dict[str, Any]]:
    """Return the last *n* JSON objects from a JSONL file."""
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


def _read_json_or_summary(path: Path) -> list[dict[str, Any]]:
    """Read a file that may be a single JSON object or JSONL lines."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []
    except json.JSONDecodeError:
        return _tail_jsonl(path, 1000)


def _read_cmc_cache(db_path: Path) -> dict[str, Any]:
    """Query CMC premium SQLite for fear/greed, market metrics, funding, token info, x402 payments."""
    if not db_path.exists():
        return {}
    out: dict[str, Any] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
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
            # Optional tables — may not exist in all schemas
            try:
                rows = conn.execute(
                    "SELECT name, symbol, cmc_rank, timestamp FROM token_info ORDER BY timestamp DESC LIMIT 5"
                ).fetchall()
                if rows:
                    out["token_info"] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            try:
                rows = conn.execute(
                    "SELECT amount, asset, endpoint, tx_hash, timestamp FROM x402_payments ORDER BY timestamp DESC LIMIT 3"
                ).fetchall()
                if rows:
                    out["x402_payments"] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
    except Exception as exc:
        LOGGER.warning("CMC cache read failed: %s", exc)
    return out


def _safe_field(value: object, max_len: int = 200) -> str:
    """Truncate and strip LLM heading markers to reduce prompt injection surface."""
    return str(value)[:max_len].replace("###", "---")


def _build_context(
    health_snapshot: dict[str, Any] | None,
    decision_log_path: Path,
    cmc_db_path: Path,
) -> str:
    """Assemble a structured markdown context string from all available data sources."""
    sections: list[str] = []

    # 1. Bot health
    health = health_snapshot or {}
    if health:
        lines = [
            f"- Status: {health.get('status', 'unknown')}",
            f"- Open positions: {health.get('positions', 0)}",
            f"- Daily trades: {health.get('daily_trades', 0)}",
            f"- Drawdown: {health.get('drawdown_pct', 0)}%",
        ]
        if health.get("last_cycle"):
            lines.append(f"- Last cycle: {health['last_cycle']}")
        if health.get("ml_mode"):
            lines.append(f"- ML mode: {health['ml_mode']}")
        sections.append("### Bot Health\n" + "\n".join(lines))
    else:
        sections.append("### Bot Health\n[No data yet]")

    # 2. CMC data
    cmc = _read_cmc_cache(cmc_db_path)
    fg = cmc.get("fear_greed")
    if fg:
        sections.append(
            f"### Fear & Greed\n- Value: {fg.get('value')} ({fg.get('classification')}) as of {fg.get('timestamp')}"
        )
    else:
        sections.append("### Fear & Greed\n[No data yet]")

    mm = cmc.get("market_metrics")
    if mm:
        sections.append(
            f"### Market Metrics\n- BTC dominance: {mm.get('btc_dominance')}%\n- Altcoin volume: {mm.get('altcoin_volume')}\n- As of: {mm.get('timestamp')}"
        )
    else:
        sections.append("### Market Metrics\n[No data yet]")

    rates = cmc.get("funding_rates")
    if rates:
        lines = [f"- {r.get('token')}: funding={r.get('funding_rate')} OI={r.get('open_interest')} @ {r.get('timestamp')}" for r in rates]
        sections.append("### Top 5 Funding Rates\n" + "\n".join(lines))
    else:
        sections.append("### Top 5 Funding Rates\n[No data yet]")

    # 3. Trading decisions
    # The parameter may point to a non-existent file; fall back to logs/decision_live.jsonl
    dlog_path = decision_log_path if decision_log_path.exists() else Path("logs/decision_live.jsonl")
    decisions = _tail_jsonl(dlog_path, 3) if dlog_path.exists() else []
    if not decisions:
        sections.append("### Latest Trading Decisions\n[No data yet]")
    else:
        lines = []
        for row in decisions:
            ts = _safe_field(row.get("timestamp", "?"), 30)
            action = _safe_field(row.get("action", "WAIT"), 20)
            symbol = _safe_field(row.get("symbol", "-"), 20)
            reasons = row.get("reasons", [])
            reason = _safe_field(reasons[0] if reasons else row.get("reason", ""), 80)
            regime = _safe_field(row.get("regime", "unknown"), 30)
            lines.append(f"- {ts}: {action} {symbol} — {reason} (regime: {regime})")
        sections.append("### Latest Trading Decisions\n" + "\n".join(lines))

    # 4. x402 payments
    x402_spend = _read_json_or_summary(Path("logs/x402_spend.json"))
    x402_calls = _tail_jsonl(Path("logs/x402_calls.jsonl"), 3)
    # Prefer detailed call records; fall back to summary object
    x402_records = x402_calls if x402_calls else x402_spend
    if x402_records:
        lines = []
        for row in x402_records[-3:]:
            ts = row.get("ts") or row.get("timestamp", "?")
            amount = row.get("amount_usdc") or row.get("amount", "?")
            endpoint = row.get("tool") or row.get("endpoint", "?")
            outcome = row.get("outcome", "?")
            reason = row.get("reason", "")
            total = row.get("total_spend_usdc", "")
            total_str = f" | total spent: {total}" if total != "" else ""
            lines.append(f"- {ts}: {amount} USDC → {endpoint} (outcome: {outcome}{total_str}) {reason}")
        sections.append("### Latest x402 Payments\n" + "\n".join(lines))
    else:
        sections.append("### Latest x402 Payments\n[No data yet]")

    # 5. Trade outcomes
    outcomes = _tail_jsonl(Path("logs/trade_outcomes.jsonl"), 3)
    if outcomes:
        lines = []
        for row in outcomes:
            ts = row.get("timestamp", "?")
            symbol = row.get("symbol", "?")
            pnl = row.get("pnl", "?")
            exit_reason = row.get("exit_reason", "?")
            side = row.get("side", "?")
            lines.append(f"- {ts}: {symbol} {side} PnL={pnl} ({exit_reason})")
        sections.append("### Latest Trade Outcomes\n" + "\n".join(lines))
    else:
        sections.append("### Latest Trade Outcomes\n[No data yet]")

    # 6. Portfolio snapshot
    portfolio = _tail_jsonl(Path("logs/portfolio_snapshots.jsonl"), 1)
    if portfolio:
        p = portfolio[0]
        total = p.get("portfolio_value_usdc") or p.get("total_usd", "?")
        cash = p.get("cash_usdc") or p.get("cash_usd", "?")
        exposure = p.get("exposure", "?")
        drawdown = p.get("drawdown_pct", "?")
        sections.append(
            f"### Latest Portfolio Snapshot\n"
            f"- Total USD: {total}\n"
            f"- Cash USD: {cash}\n"
            f"- Exposure: {exposure}\n"
            f"- Drawdown: {drawdown}%"
        )
    else:
        sections.append("### Latest Portfolio Snapshot\n[No data yet]")

    return "\n\n".join(sections)


def _legacy_reply(
    text: str,
    health_snapshot: dict[str, Any] | None,
    decision_log_path: Path,
    cmc_db_path: Path,
) -> dict[str, str]:
    """Return the legacy rule-based reply for known keywords, or None if no keyword matches."""
    health = health_snapshot or {}
    cmc = _read_cmc_cache(cmc_db_path)
    decisions = _tail_jsonl(decision_log_path, 3) if decision_log_path.exists() else []

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
            return {"reply": "No recent decisions in decision_log.jsonl.", "source": str(decision_log_path)}
        lines = []
        for row in decisions:
            lines.append(
                f"{row.get('timestamp', '?')}: {row.get('action', 'WAIT')} "
                f"{row.get('symbol', '-')} — {str(row.get('reason', ''))[:60]}"
            )
        return {"reply": "\n".join(lines), "source": str(decision_log_path)}

    if any(k in text for k in ("x402", "payment", "spend")):
        x402_spend = _read_json_or_summary(Path("logs/x402_spend.json"))
        x402_calls = _tail_jsonl(Path("logs/x402_calls.jsonl"), 3)
        records = x402_calls if x402_calls else x402_spend
        if records:
            lines = []
            for row in records[-3:]:
                ts = row.get("ts") or row.get("timestamp", "?")
                amount = row.get("amount_usdc") or row.get("amount", "?")
                endpoint = row.get("tool") or row.get("endpoint", "?")
                outcome = row.get("outcome", "?")
                lines.append(f"{ts}: {amount} USDC → {endpoint} (outcome: {outcome})")
            return {"reply": "\n".join(lines), "source": "x402 logs"}
        return {"reply": "No x402 payment logs found yet.", "source": "x402 logs"}

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


def build_chat_reply(
    message: str,
    *,
    session_id: str = "default",
    health_snapshot: dict[str, Any] | None = None,
    decision_log_path: str | Path = "decision_log.jsonl",
    cmc_db_path: str | Path = "data/cmc_premium.db",
) -> dict[str, str]:
    """Return a chat reply dict with ``reply`` and ``source`` keys.

    If ``KIMI_API_KEY`` is set, the reply is generated by the Kimi LLM with
    live injected context. Otherwise it falls back to legacy rule-based replies.
    """
    text = (message or "").strip().lower()
    decision_path = Path(decision_log_path)
    cmc_path = Path(cmc_db_path)

    client = _get_client()
    if client is None:
        # Missing API key: return legacy reply with a friendly prefix
        legacy = _legacy_reply(text, health_snapshot, decision_path, cmc_path)
        legacy["reply"] = _MISSING_KEY_MSG + legacy["reply"]
        return legacy

    model = os.getenv("KIMI_MODEL", "moonshot-v1-8k")

    try:
        context = _build_context(health_snapshot, decision_path, cmc_path)
        system_prompt = (
            "You are the Market Terminal for an autonomous BNB Chain trading bot competing in a hackathon.\n"
            "You have read-only access to live bot telemetry, x402 micropayment logs, CoinMarketCap premium data, and autonomous trading decisions.\n"
            "Answer the user's question concisely using ONLY the context provided below.\n"
            "If the data is not in the context, say \"I don't have that data yet\" rather than hallucinating.\n"
            "Keep responses under 200 words. Use markdown for numbers and tables where helpful.\n\n"
            f"Current Context:\n{context}"
        )

        # Manage multi-turn history
        with _session_lock:
            history = _sessions.get(session_id, [])
            messages = [
                {"role": "system", "content": system_prompt},
            ] + history[-6:] + [
                {"role": "user", "content": message},
            ]

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=512,
            timeout=15,
        )

        assistant_text = response.choices[0].message.content or ""
        usage = response.usage
        token_info = f" tokens={usage.total_tokens}" if usage else ""
        LOGGER.info(
            'Chat request: "%s..." → model=%s%s',
            message[:40],
            model,
            token_info,
        )

        # Store turn
        with _session_lock:
            if session_id not in _sessions:
                if len(_sessions) >= _MAX_SESSIONS:
                    _sessions.pop(next(iter(_sessions)))  # evict oldest (insertion-order)
                _sessions[session_id] = []
            _sessions[session_id].append({"role": "user", "content": message})
            _sessions[session_id].append({"role": "assistant", "content": assistant_text})
            # Trim to max 6 past messages (3 turns)
            if len(_sessions[session_id]) > 6:
                _sessions[session_id] = _sessions[session_id][-6:]

        return {
            "reply": assistant_text,
            "source": f"{model} · live context",
        }

    except Exception as exc:
        LOGGER.warning("Kimi API call failed: %s", exc)
        # API failure: fall back to legacy rule-based replies for all queries
        return _legacy_reply(text, health_snapshot, decision_path, cmc_path)
