"""Shadow-only sentiment sources for offline validation.

Example:
    tier2 = SentimentTier2(log_path="logs/sentiment_shadow.jsonl")
    tier2.log_keyword_count("bullish breakout risk")

Interface contract:
    Imports: standard library only plus shared logging schema.
    Exports: SentimentTier2.
    Does not feed live decisions, sizing, execution, or guardrails.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional

from src.common.logging_schema import SentimentShadowLog, append_to_file


class SentimentTier2:
    """Shadow-only sentiment collectors that write separated logs."""

    TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    def __init__(
        self,
        alternative_me_base: str = "https://api.alternative.me",
        bsc_rpc_url: str = "",
        log_path: str = "logs/sentiment_shadow.jsonl",
        min_rpc_interval_seconds: int = 300,
    ) -> None:
        self.alternative_me_base = alternative_me_base.rstrip("/")
        self.bsc_rpc_url = bsc_rpc_url
        self.log_path = log_path
        self.min_rpc_interval_seconds = max(0, int(min_rpc_interval_seconds))
        self._last_transfer_call_by_token: dict[str, float] = {}

    def log_alternative_me_fng(self) -> Optional[dict]:
        """Fetch Alternative.me F&G and write a shadow sentiment log."""

        data = self._fetch_json(f"{self.alternative_me_base}/fng/")
        if not data:
            return None
        try:
            latest = data["data"][0]
            value = float(latest["value"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        self._append("alternative_me_fng", value, _score_fng(value), "OBSERVE")
        return {"value": value, "classification": latest.get("value_classification")}

    def log_bsc_transfer_count(self, token_address: str, blocks: int = 5000) -> Optional[int]:
        """Fetch recent Transfer log count with a per-token rate limit."""

        token = token_address.lower()
        now = time.time()
        last_call = self._last_transfer_call_by_token.get(token, 0.0)
        if now - last_call < self.min_rpc_interval_seconds or not self.bsc_rpc_url:
            return None
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{"address": token_address, "topics": [self.TRANSFER_TOPIC]}],
            "id": 1,
        }
        data = self._post_json(self.bsc_rpc_url, payload)
        self._last_transfer_call_by_token[token] = now
        if not data or not isinstance(data.get("result"), list):
            return None
        count = len(data["result"])
        self._append("bsc_transfer_count", float(count), 0.0, "OBSERVE")
        return count

    def log_keyword_count(self, text_source: str) -> Optional[dict]:
        """Count simple bullish/bearish keywords and log the balance."""

        text = text_source.lower()
        bullish = sum(text.count(word) for word in ("bull", "breakout", "pump", "rally", "long"))
        bearish = sum(text.count(word) for word in ("bear", "dump", "crash", "short", "risk"))
        if bullish == 0 and bearish == 0:
            return None
        score = (bullish - bearish) / max(1, bullish + bearish)
        self._append("keyword_count", float(bullish - bearish), score, "OBSERVE")
        return {"bullish": bullish, "bearish": bearish, "sentiment_score": score}

    def _append(self, metric: str, value: float, score: float, recommendation: str) -> None:
        append_to_file(
            self.log_path,
            SentimentShadowLog(
                metric=metric,
                value=value,
                sentiment_score=score,
                shadow_recommendation=recommendation,
            ),
        )

    @staticmethod
    def _fetch_json(url: str) -> Optional[dict]:
        try:
            request = urllib.request.Request(url)
            request.add_header("Accept", "application/json")
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _post_json(url: str, payload: dict) -> Optional[dict]:
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


def _score_fng(value: float) -> float:
    if value > 75:
        return -1.0
    if value < 20:
        return 0.5
    return 0.0
