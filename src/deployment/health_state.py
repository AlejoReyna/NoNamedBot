"""Shared runtime state for the health check HTTP server."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class HealthState:
    """Thread-safe snapshot updated by the trading loop."""

    last_cycle_at: datetime | None = None
    positions: int = 0
    daily_trades: int = 0
    drawdown_pct: float = 0.0
    status: str = "starting"
    x402_wallet_address: str | None = None
    x402_usdc_balance: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last_cycle = self.last_cycle_at.isoformat() if self.last_cycle_at else None
            return {
                "status": self.status,
                "last_cycle": last_cycle,
                "positions": self.positions,
                "daily_trades": self.daily_trades,
                "drawdown_pct": round(self.drawdown_pct, 4),
                "x402": {
                    "walletAddress": self.x402_wallet_address,
                    "walletUsdcBalance": self.x402_usdc_balance,
                },
            }

    def is_stalled(self, stall_minutes: float = 15.0) -> bool:
        with self._lock:
            if self.last_cycle_at is None:
                return False
            age = (datetime.now(timezone.utc) - self.last_cycle_at).total_seconds() / 60.0
            return age > stall_minutes
