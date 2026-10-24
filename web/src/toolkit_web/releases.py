from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from toolkit_web.discord_oauth import TIMEOUT_SECONDS
from toolkit_web.token_probe import USER_AGENT

REPO = "Wolklaw/OSRS-Toolkit"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

REFRESH_SECONDS = 3600


@dataclass(frozen=True)
class Download:
    name: str
    url: str
    size_mb: float


@dataclass(frozen=True)
class Release:
    version: str
    published_on: str
    installer: Download | None
    portable: Download | None


def _download(asset: dict) -> Download | None:
    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not isinstance(url, str) or not isinstance(name, str):
        return None
    size = asset.get("size")
    size_mb = round(size / 1_000_000, 1) if isinstance(size, int) else 0.0
    return Download(name=name, url=url, size_mb=size_mb)


def _parse(payload: dict) -> Release | None:
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    assets = payload.get("assets")
    assets = assets if isinstance(assets, list) else []
    installer = portable = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "")
        if not isinstance(name, str):
            continue
        if installer is None and name.lower().endswith(".exe"):
            installer = _download(asset)
        elif portable is None and name.lower().endswith(".zip"):
            portable = _download(asset)
    published = payload.get("published_at")
    return Release(
        version=tag.lstrip("v"),
        published_on=published[:10] if isinstance(published, str) else "",
        installer=installer,
        portable=portable,
    )


def fetch_latest() -> Release | None:
    request = urllib.request.Request(LATEST_API_URL, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None
    return _parse(payload) if isinstance(payload, dict) else None


class ReleaseCache:
    def __init__(self, fetch: Callable[[], Release | None] | None = None) -> None:
        self._fetch = fetch
        self._lock = threading.Lock()
        self._release: Release | None = None
        self._fetched_at: float = 0.0

    def latest(self) -> Release | None:
        with self._lock:
            if self._release is None or time.monotonic() - self._fetched_at > REFRESH_SECONDS:
                fetched = (self._fetch or fetch_latest)()
                if fetched is not None:
                    self._release = fetched
                    self._fetched_at = time.monotonic()
            return self._release
