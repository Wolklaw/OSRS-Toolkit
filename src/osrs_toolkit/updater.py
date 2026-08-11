from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osrs_toolkit import __version__

LATEST_RELEASE_URL = "https://api.github.com/repos/Wolklaw/OSRS-Toolkit/releases/latest"
USER_AGENT = (
    f"OSRS-Toolkit-Updater/{__version__} (+https://github.com/Wolklaw/OSRS-Toolkit)"
)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    installer_name: str
    installer_url: str
    installer_digest: str


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        raise ValueError(f"Unsupported version: {value}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(latest: str, current: str) -> bool:
    return version_tuple(latest) > version_tuple(current)


def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)

    tag = str(payload.get("tag_name", "")).strip()
    version_tuple(tag)
    assets = payload.get("assets") or []
    installer = next(
        (
            asset
            for asset in assets
            if str(asset.get("name", "")).lower().endswith(".exe")
            and "setup" in str(asset.get("name", "")).lower()
        ),
        None,
    )
    if installer is None:
        raise RuntimeError("The latest release does not contain a Windows installer.")

    digest = installer.get("digest")
    if not digest or not str(digest).startswith("sha256:"):
        raise RuntimeError("The installer does not include a SHA-256 security digest.")
    return ReleaseInfo(
        version=tag.removeprefix("v"),
        page_url=str(payload.get("html_url", "")),
        installer_name=str(installer["name"]),
        installer_url=str(installer["browser_download_url"]),
        installer_digest=str(digest),
    )


def download_installer(
    release: ReleaseInfo,
    progress: Callable[[int], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    update_dir = Path(tempfile.gettempdir()) / "OSRSToolkitUpdate"
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / release.installer_name
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(release.installer_url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as file:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            while chunk := response.read(1024 * 256):
                file.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress and total:
                    progress(min(100, int(downloaded * 100 / total)))

        expected = release.installer_digest.removeprefix("sha256:").lower()
        if digest.hexdigest().lower() != expected:
            raise RuntimeError("The downloaded installer failed its security check.")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def launch_installer(path: Path) -> None:
    subprocess.Popen([str(path)], close_fds=True)
