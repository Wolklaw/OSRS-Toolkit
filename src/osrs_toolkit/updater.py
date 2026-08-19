from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osrs_toolkit import __version__

LATEST_RELEASE_URL = "https://api.github.com/repos/Wolklaw/OSRS-Toolkit/releases/latest"
USER_AGENT = (
    f"OSRS-Toolkit-Updater/{__version__} (+https://github.com/Wolklaw/OSRS-Toolkit)"
)
_FINALIZE_ATTEMPTS = 5
_FINALIZE_RETRY_SECONDS = 0.4


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
        _finalize_download(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def _finalize_download(partial: Path, destination: Path) -> None:
    """Move the verified download into place, retrying past a transient file lock.

    A file just written to disk is a favorite target for antivirus real-time scanning,
    which can hold it open for a moment and make the rename fail with Windows error 5
    ("Access is denied") even though nothing is actually wrong. Retrying briefly clears
    that up; a destination genuinely locked by something else (e.g. a copy of the
    installer left running from an earlier attempt) keeps failing and gets a clearer,
    actionable message instead of the raw OS error.
    """
    last_error: OSError | None = None
    for attempt in range(_FINALIZE_ATTEMPTS):
        try:
            partial.replace(destination)
            return
        except OSError as exc:
            last_error = exc
            if attempt < _FINALIZE_ATTEMPTS - 1:
                time.sleep(_FINALIZE_RETRY_SECONDS)
    raise RuntimeError(
        f"Could not finish preparing the installer at {destination}. It may still be "
        "open in another window — including a previous copy of this installer — or held "
        "by antivirus scanning. Close any open installer windows and try again."
    ) from last_error


def launch_installer(path: Path) -> None:
    subprocess.Popen([str(path)], close_fds=True)
