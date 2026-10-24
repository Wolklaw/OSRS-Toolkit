from __future__ import annotations

import threading
import time
from pathlib import Path

from osrs_toolkit.market import MarketDataError, WikiMarketClient
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint

REFRESH_SECONDS = 300


class MarketCache:
    def __init__(self, cache_dir: Path) -> None:
        self._client = WikiMarketClient(cache_dir=cache_dir)
        self._lock = threading.Lock()
        self._mappings: dict[int, ItemMapping] = {}
        self._points: list[MarketPoint] = []
        self._fetched_at: float = 0.0

    def snapshot(self) -> tuple[dict[int, ItemMapping], list[MarketPoint]]:
        with self._lock:
            if time.monotonic() - self._fetched_at > REFRESH_SECONDS:
                try:
                    self._mappings, self._points = self._client.fetch_snapshot()
                    self._fetched_at = time.monotonic()
                except MarketDataError:
                    if not self._mappings:
                        raise
            return self._mappings, self._points

    def age_seconds(self) -> float | None:
        if not self._fetched_at:
            return None
        return max(0.0, time.monotonic() - self._fetched_at)

    def invalidate(self) -> None:
        with self._lock:
            self._fetched_at = 0.0

    def timeseries(self, item_id: int) -> list[TimeseriesPoint]:
        try:
            return self._client.fetch_timeseries(item_id)
        except MarketDataError:
            return []
