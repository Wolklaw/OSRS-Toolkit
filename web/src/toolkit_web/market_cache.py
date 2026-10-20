"""One shared, periodically refreshed read of the OSRS Wiki's prices.

Market prices are the same number for every signed-in account — there is no reason for two
people looking at the flip suggestions page in the same minute to each trigger their own fetch
of the wiki's full item mapping, which is itself a multi-megabyte response. Held once per
process, behind a lock so two requests racing to refresh it do not both pay for the fetch.

``WikiMarketClient`` already falls back to its own on-disk cache when the wiki cannot be
reached, so a request only ever sees ``MarketDataError`` when the wiki has never answered even
once — the same "nothing to show yet" case the caller has to handle regardless.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from osrs_toolkit.market import MarketDataError, WikiMarketClient
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint

#: How long a snapshot is trusted before the next request pays to refresh it. The wiki's own
#: volume windows are 5 and 60 minutes, so refreshing more often than the shorter of those
#: would only ever return numbers that have not actually changed.
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
                    # The client's own on-disk cache already tried and failed too, or there is
                    # nothing to show yet. Whatever was last held (possibly nothing) stands, and
                    # _fetched_at is deliberately not updated — the very next request tries
                    # again rather than waiting out the full window, since a request this
                    # infrequent is never at risk of hammering the wiki during an outage.
                    if not self._mappings:
                        raise
            return self._mappings, self._points

    def age_seconds(self) -> float | None:
        """How long ago the held snapshot was fetched, or ``None`` before the first one lands.

        What the header's price pill reads. Deliberately not derived from the wiki's own
        timestamps on individual prices — those say when an item last traded, which is a
        different and much older number for a quiet item, and answers a question nobody asked
        when what they want to know is whether this page is showing them a fresh read.
        """
        if not self._fetched_at:
            return None
        return max(0.0, time.monotonic() - self._fetched_at)

    def invalidate(self) -> None:
        """Drop the refresh window so the next ``snapshot`` pays for a fresh fetch.

        The held snapshot itself is kept: if the wiki cannot be reached on that next call,
        stale prices still beat an empty page, which is the same trade ``snapshot`` already
        makes when a scheduled refresh fails.
        """
        with self._lock:
            self._fetched_at = 0.0

    def timeseries(self, item_id: int) -> list[TimeseriesPoint]:
        """One item's price history. Not held behind the snapshot's own refresh window — the
        wiki client already caches each item's series to disk on its own, and a request for an
        item nobody has looked at yet should not wait on somebody else's unrelated refresh."""
        try:
            return self._client.fetch_timeseries(item_id)
        except MarketDataError:
            return []
