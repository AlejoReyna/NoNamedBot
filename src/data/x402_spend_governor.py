"""Hard budget governor for CMC x402 micropayments.

Every paid call must pass through ``allow_call()`` first. The governor
enforces a daily and a total (competition-window) budget, applies a cooldown
after failed paid calls so a broken endpoint cannot re-bill every loop cycle,
and persists a spend ledger to disk so restarts do not reset the budget.

Degradation is always graceful: when the governor refuses, callers fall back
to the free keyless REST layer, which carries every field the strategy's
entry gates actually require.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from src.common.logging_schema import append_to_file

LOGGER = logging.getLogger(__name__)
SENSITIVE_HEX_RE = re.compile(r"0x[a-fA-F0-9]{16,}")
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_+/=-]{80,}\b")


@dataclass(frozen=True)
class X402CallLog:
    ts: str
    outcome: Literal["success", "failure"]
    tool: str | None
    amount_usdc: float
    http_status: int | None
    reason: str | None
    daily_spend_usdc: float
    total_spend_usdc: float


class X402SpendGovernor:
    """Enforce daily/total x402 spend caps with failure cooldown."""

    def __init__(
        self,
        daily_budget_usdc: float,
        total_budget_usdc: float,
        cost_per_call_usdc: float,
        failure_cooldown_seconds: int = 900,
        ledger_path: str | Path = "logs/x402_spend.json",
        call_log_path: str | Path = "logs/x402_calls.jsonl",
        budget_circuit: Any | None = None,
    ) -> None:
        self.daily_budget_usdc = max(0.0, float(daily_budget_usdc))
        self.total_budget_usdc = max(0.0, float(total_budget_usdc))
        self.cost_per_call_usdc = max(0.0, float(cost_per_call_usdc))
        self.failure_cooldown_seconds = max(0, int(failure_cooldown_seconds))
        self.ledger_path = Path(ledger_path)
        self.call_log_path = Path(call_log_path)
        self.budget_circuit = budget_circuit
        self._day = self._today()
        self._daily_spend = 0.0
        self._total_spend = 0.0
        self._last_failure_monotonic: float | None = None
        self._load()

    # -- public API ---------------------------------------------------------

    def allow_call(self, calls: int = 1) -> bool:
        """Return whether ``calls`` paid requests fit the remaining budget."""

        self._roll_day_if_needed()
        cost = self.cost_per_call_usdc * max(1, calls)
        if self._in_failure_cooldown():
            LOGGER.info(
                "x402 governor: failure cooldown active (%.0fs left); using keyless fallback",
                self._cooldown_remaining(),
            )
            return False
        if self.daily_budget_usdc > 0 and self._daily_spend + cost > self.daily_budget_usdc:
            LOGGER.warning(
                "x402 governor: daily budget reached ($%.2f/$%.2f); keyless only until UTC midnight",
                self._daily_spend,
                self.daily_budget_usdc,
            )
            return False
        if self.total_budget_usdc > 0 and self._total_spend + cost > self.total_budget_usdc:
            LOGGER.warning(
                "x402 governor: total budget reached ($%.2f/$%.2f); keyless only",
                self._total_spend,
                self.total_budget_usdc,
            )
            return False
        return True

    def record_spend(
        self,
        amount_usdc: float | None = None,
        *,
        tool: str | None = None,
        http_status: int | None = None,
    ) -> None:
        """Record a successful paid call (defaults to the per-call cap)."""

        self._roll_day_if_needed()
        spent = self.cost_per_call_usdc if amount_usdc is None else max(0.0, float(amount_usdc))
        self._daily_spend += spent
        self._total_spend += spent
        self._last_failure_monotonic = None
        self._save()
        self._append_call_record(
            outcome="success",
            amount_usdc=spent,
            tool=tool,
            http_status=http_status,
            reason=None,
        )
        if self.budget_circuit is not None:
            self.budget_circuit.record(spent)

    def record_failure(
        self,
        assume_charged: bool = True,
        *,
        tool: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a failed paid call and start the retry cooldown.

        ``assume_charged`` budgets conservatively: a call that failed after the
        402 payment settled still spent money, so count it unless the failure
        is known to have happened before payment.
        """

        self._roll_day_if_needed()
        charged = self.cost_per_call_usdc if assume_charged else 0.0
        if charged:
            self._daily_spend += charged
            self._total_spend += charged
        self._last_failure_monotonic = time.monotonic()
        self._save()
        self._append_call_record(
            outcome="failure",
            amount_usdc=charged,
            tool=tool,
            http_status=None,
            reason=reason,
        )
        if self.budget_circuit is not None:
            self.budget_circuit.record(charged)

    def snapshot(self) -> dict[str, float | str | bool]:
        """Telemetry payload for logs and the health endpoint."""

        self._roll_day_if_needed()
        return {
            "day": self._day,
            "daily_spend_usdc": round(self._daily_spend, 4),
            "daily_budget_usdc": self.daily_budget_usdc,
            "total_spend_usdc": round(self._total_spend, 4),
            "total_budget_usdc": self.total_budget_usdc,
            "failure_cooldown_active": self._in_failure_cooldown(),
        }

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._daily_spend = 0.0
            self._save()

    def _in_failure_cooldown(self) -> bool:
        return self._cooldown_remaining() > 0

    def _cooldown_remaining(self) -> float:
        if self._last_failure_monotonic is None or self.failure_cooldown_seconds <= 0:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_monotonic
        return max(0.0, self.failure_cooldown_seconds - elapsed)

    def _load(self) -> None:
        if not self.ledger_path.exists():
            return
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("x402 governor: could not read ledger %s: %s", self.ledger_path, exc)
            return
        if not isinstance(payload, dict):
            return
        self._total_spend = float(payload.get("total_spend_usdc", 0.0))
        if str(payload.get("day", "")) == self._day:
            self._daily_spend = float(payload.get("daily_spend_usdc", 0.0))

    def _save(self) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.ledger_path.write_text(
                json.dumps(
                    {
                        "day": self._day,
                        "daily_spend_usdc": round(self._daily_spend, 6),
                        "total_spend_usdc": round(self._total_spend, 6),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("x402 governor: could not persist ledger: %s", exc)

    def _append_call_record(
        self,
        *,
        outcome: Literal["success", "failure"],
        amount_usdc: float,
        tool: str | None,
        http_status: int | None,
        reason: str | None,
    ) -> None:
        record = X402CallLog(
            ts=datetime.now(timezone.utc).isoformat(),
            outcome=outcome,
            tool=tool,
            amount_usdc=round(max(0.0, float(amount_usdc)), 6),
            http_status=http_status,
            reason=self._redact_reason(reason),
            daily_spend_usdc=round(self._daily_spend, 6),
            total_spend_usdc=round(self._total_spend, 6),
        )
        try:
            append_to_file(self.call_log_path, record)
        except OSError as exc:
            LOGGER.warning("x402 governor: could not persist call log: %s", exc)

    @staticmethod
    def _redact_reason(reason: str | None, limit: int = 200) -> str | None:
        if reason is None:
            return None

        value = " ".join(str(reason).split())
        value = SENSITIVE_HEX_RE.sub("0x[redacted]", value)
        value = LONG_TOKEN_RE.sub("[redacted]", value)
        if len(value) <= limit:
            return value
        return f"{value[: limit - 3]}..."


class BudgetCircuitBreaker:
    """Soft throttle layer above X402SpendGovernor.

    When realized daily spend exceeds the planned budget, doubles the
    hot-candidate refresh interval (T2) to cut call volume by ~50% without
    fully stopping enrichment.  Resets at UTC midnight alongside the governor.
    """

    def __init__(self, daily_budget: float, headroom: float = 0.25) -> None:
        self.daily_budget = max(0.0, float(daily_budget))
        self.headroom = max(0.0, float(headroom))
        self._actual_spend: float = 0.0
        self._cycles_today: int = 0
        self._day: str = self._today()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._actual_spend = 0.0
            self._cycles_today = 0

    def record(self, cost_per_cycle: float) -> bool:
        """Accumulate spend and return True if still within budget+headroom."""
        self._roll_day_if_needed()
        self._actual_spend += max(0.0, float(cost_per_cycle))
        self._cycles_today += 1
        return self._actual_spend <= self.daily_budget * (1.0 + self.headroom)

    def throttled_refresh_age(self, base_refresh_age_seconds: int) -> int:
        """Return (possibly doubled) refresh interval when over daily budget."""
        self._roll_day_if_needed()
        if self._actual_spend > self.daily_budget:
            return base_refresh_age_seconds * 2
        return base_refresh_age_seconds

    def snapshot(self) -> dict[str, float | int | str | bool]:
        self._roll_day_if_needed()
        return {
            "day": self._day,
            "actual_spend_usdc": round(self._actual_spend, 4),
            "daily_budget_usdc": self.daily_budget,
            "cycles_today": self._cycles_today,
            "over_budget": self._actual_spend > self.daily_budget,
        }
