"""What the newest desktop release is, asked of GitHub and remembered for a while.

The download page could just link at ``/releases/latest`` and let GitHub draw the list, but
then the site cannot say which version it is offering, how big it is, or when it landed —
which is most of what somebody weighing "do I want this?" is actually asking. So the release
is read once an hour and rendered here instead.

Nothing depends on this succeeding. A page with no release to show still links to the
releases page, which is where the download would have pointed anyway.
"""

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

#: GitHub allows 60 unauthenticated calls an hour from one address. One an hour leaves that
#: budget untouched, and a release that is an hour old on the page is a release nobody has
#: noticed yet anyway.
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
    """The two assets a release is expected to carry, picked by extension rather than by name.

    Matching on the file type rather than on ``OSRS-Toolkit-Setup-<version>.exe`` means the
    build can rename or re-version its artifacts without this page quietly losing a button.
    """
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
    """One shared read of the latest release, refreshed on a timer like the market snapshot.

    Whatever was last fetched is kept when a refresh fails, so a GitHub outage costs the page
    its freshness rather than its content.
    """

    def __init__(self, fetch: Callable[[], Release | None] | None = None) -> None:
        #: Looked up at call time rather than bound here, so a test can replace the module's
        #: fetcher without having to reach inside an app that already built its cache.
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
