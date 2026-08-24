from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from osrs_toolkit import __version__
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint


class MarketDataError(RuntimeError):
    pass


# How long the item mapping (name, members, buy limit, high alch) is trusted before
# refetching. It rarely changes, so there's no need to redownload ~900KB every 5-minute
# price cycle just to learn nothing changed.
MAPPING_TTL_SECONDS = 86_400

# How many per-item price-history files the cache keeps. One per item whose details are
# opened; a fallback for when the wiki is unreachable, not worth keeping forever.
MAX_TIMESERIES_CACHE_FILES = 200


class WikiMarketClient:
    BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"
    USER_AGENT = (
        f"OSRSToolkit/{__version__} desktop market analysis "
        "(+https://github.com/Wolklaw/OSRS-Toolkit)"
    )

    def __init__(self, cache_dir: Path | None = None, timeout: float = 12.0) -> None:
        local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
        self.cache_dir = cache_dir or local_data / "OSRSToolkit" / "cache"
        self.timeout = timeout
        self.used_cache = False
        self._mappings: dict[int, ItemMapping] | None = None
        self._mappings_fetched_at = 0.0

    def _get(
        self, route: str, *, cache_key: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch ``route`` from the API, caching the response under ``cache_key``.

        ``cache_key`` defaults to ``route``, which is a safe filename for every existing
        caller. Routes with query params (``fetch_timeseries``) must pass a filesystem-safe
        key instead, since "?" isn't legal in a Windows filename.

        A cache write failure (full disk, AV lock, etc.) must not throw away prices that
        already downloaded fine — only the download/parse decide success.
        """
        request = urllib.request.Request(
            f"{self.BASE_URL}/{route}",
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        cache_file = self.cache_dir / f"{cache_key or route}.json"
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                payload = json.loads(body.decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            if cache_file.exists():
                try:
                    self.used_cache = True
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            raise MarketDataError(f"Could not load {route} market data") from exc
        self._write_cache(cache_file, payload)
        return payload

    @staticmethod
    def _write_cache(cache_file: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # Caller already has the fresh data; next run just falls back further if needed.
            pass

    def _fetch_mappings(self) -> dict[int, ItemMapping]:
        """The item mapping, reused for ``MAPPING_TTL_SECONDS`` before it is fetched again.

        Cached as the built dict, not the raw payload, so a reuse skips the parse too.
        """
        cached = self._mappings
        if cached is not None and time.monotonic() - self._mappings_fetched_at < MAPPING_TTL_SECONDS:
            return cached
        mapping_raw = self._get("mapping")
        if not isinstance(mapping_raw, list):
            raise MarketDataError("Unexpected item mapping response")
        mappings = {
            int(item["id"]): ItemMapping(
                item_id=int(item["id"]),
                name=str(item["name"]),
                members=bool(item.get("members", False)),
                buy_limit=_positive_int(item.get("limit")),
                high_alch=_positive_int(item.get("highalch")),
            )
            for item in mapping_raw
        }
        self._mappings = mappings
        self._mappings_fetched_at = time.monotonic()
        return mappings

    def fetch_snapshot(self) -> tuple[dict[int, ItemMapping], list[MarketPoint]]:
        self.used_cache = False
        mappings = self._fetch_mappings()
        latest_raw = self._get("latest")
        five_raw = self._get("5m")
        hour_raw = self._get("1h")
        latest = latest_raw.get("data", {}) if isinstance(latest_raw, dict) else {}
        five = five_raw.get("data", {}) if isinstance(five_raw, dict) else {}
        hour = hour_raw.get("data", {}) if isinstance(hour_raw, dict) else {}
        points: list[MarketPoint] = []
        for item_key, price in latest.items():
            high, low = price.get("high"), price.get("low")
            high_time, low_time = price.get("highTime"), price.get("lowTime")
            if not all(
                isinstance(value, int) and value > 0 for value in (high, low, high_time, low_time)
            ):
                continue
            five_item = five.get(item_key, {})
            hour_item = hour.get(item_key, {})
            high_volume_5m = int(five_item.get("highPriceVolume") or 0)
            low_volume_5m = int(five_item.get("lowPriceVolume") or 0)
            high_volume_1h = int(hour_item.get("highPriceVolume") or 0)
            low_volume_1h = int(hour_item.get("lowPriceVolume") or 0)
            points.append(
                MarketPoint(
                    item_id=int(item_key),
                    high=high,
                    low=low,
                    high_time=high_time,
                    low_time=low_time,
                    volume_5m=high_volume_5m + low_volume_5m,
                    volume_1h=high_volume_1h + low_volume_1h,
                    average_5m=_midpoint(five_item),
                    average_1h=_midpoint(hour_item),
                    average_high_5m=_price(five_item.get("avgHighPrice")),
                    average_low_5m=_price(five_item.get("avgLowPrice")),
                    average_high_1h=_price(hour_item.get("avgHighPrice")),
                    average_low_1h=_price(hour_item.get("avgLowPrice")),
                    high_volume_5m=high_volume_5m,
                    low_volume_5m=low_volume_5m,
                    high_volume_1h=high_volume_1h,
                    low_volume_1h=low_volume_1h,
                )
            )
        return mappings, points

    def fetch_timeseries(self, item_id: int, timestep: str = "6h") -> list[TimeseriesPoint]:
        """Historical average instant-buy/sell prices for one item, oldest first.

        The wiki caps each response at 300 points, so "6h" covers roughly the last 75 days.
        """
        payload = self._get(
            f"timeseries?timestep={timestep}&id={item_id}",
            cache_key=f"timeseries_{timestep}_{item_id}",
        )
        self._prune_timeseries_cache()
        return _parse_timeseries(payload)

    def _prune_timeseries_cache(self) -> None:
        """Keep the newest ``MAX_TIMESERIES_CACHE_FILES`` per-item history files.

        Nothing else removes these (~45KB each, one per item page opened), so the directory
        only grows otherwise. Pruned here rather than on a timer since this call just did a
        network fetch anyway.
        """
        try:
            files = sorted(
                self.cache_dir.glob("timeseries_*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in files[MAX_TIMESERIES_CACHE_FILES:]:
                stale.unlink(missing_ok=True)
        except OSError:
            # Housekeeping only; a cache dir that couldn't be tidied shouldn't fail the request.
            pass


def _parse_timeseries(payload: object) -> list[TimeseriesPoint]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    points: list[TimeseriesPoint] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, int) or timestamp <= 0:
            continue
        points.append(
            TimeseriesPoint(
                timestamp=timestamp,
                average_high=_price(entry.get("avgHighPrice")),
                average_low=_price(entry.get("avgLowPrice")),
                high_volume=_volume(entry.get("highPriceVolume")),
                low_volume=_volume(entry.get("lowPriceVolume")),
            )
        )
    return points


def _volume(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _price(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _midpoint(data: dict[str, Any]) -> float | None:
    prices = [data.get("avgHighPrice"), data.get("avgLowPrice")]
    valid = [float(value) for value in prices if isinstance(value, int) and value > 0]
    return sum(valid) / len(valid) if valid else None
