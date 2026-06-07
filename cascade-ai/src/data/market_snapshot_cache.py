"""TTL cache for CMC market snapshots independent of the trading loop."""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)


class MarketSnapshotCache:
    """Reuse the last CMC snapshot until its TTL expires."""

    def __init__(self) -> None:
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0

    def get_or_fetch(
        self,
        ttl_seconds: int,
        fetcher: Callable[[], dict[str, dict[str, Any]]],
        *,
        force_refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        if ttl_seconds <= 0:
            return fetcher()

        now = time.monotonic()
        age = now - self._fetched_at
        if not force_refresh and self._snapshot and age < ttl_seconds:
            LOGGER.debug(
                "Reusing CMC market snapshot (age=%.0fs ttl=%ss symbols=%s)",
                age,
                ttl_seconds,
                len(self._snapshot),
            )
            return copy.deepcopy(self._snapshot)

        snapshot = fetcher()
        self._snapshot = copy.deepcopy(snapshot)
        self._fetched_at = now
        LOGGER.info(
            "Refreshed CMC market snapshot (ttl=%ss symbols=%s)",
            ttl_seconds,
            len(snapshot),
        )
        return copy.deepcopy(snapshot)

    def reset(self) -> None:
        self._snapshot = {}
        self._fetched_at = 0.0


_DEFAULT_CACHE = MarketSnapshotCache()


def get_market_snapshot_cache() -> MarketSnapshotCache:
    return _DEFAULT_CACHE
