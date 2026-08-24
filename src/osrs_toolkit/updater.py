from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osrs_toolkit import __version__

LATEST_RELEASE_URL = "https://api.github.com/repos/Wolklaw/OSRS-Toolkit/releases/latest"
USER_AGENT = f"OSRS-Toolkit-Updater/{__version__} (+https://github.com/Wolklaw/OSRS-Toolkit)"
_FINALIZE_ATTEMPTS = 5
_FINALIZE_RETRY_SECONDS = 0.4

# Inno Setup registers the uninstall entry under the AppId in packaging/installer.iss + "_is1".
# Keep both in sync — a mismatch makes every install look portable and falls back to the wizard.
_SETUP_APP_ID = "{D8518C0E-7D14-47D9-A9D8-4030E3B25DB6}_is1"
_UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{_SETUP_APP_ID}"


@dataclass(frozen=True)
class InstallLocation:
    """A registered installation the setup program can update where it stands."""

    directory: Path
    all_users: bool


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
    update_dir = update_directory()
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / release.installer_name
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(release.installer_url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()

    try:
        with (
            urllib.request.urlopen(request, timeout=timeout) as response,
            partial.open("wb") as file,
        ):
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

    Antivirus scanning can briefly hold the file open and fail the rename with Windows
    error 5 ("Access is denied") even though nothing's wrong. Retry clears that up; a
    genuinely locked destination (e.g. installer still running) gets a clearer message
    instead of the raw OS error.
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


def update_directory() -> Path:
    """Where the downloaded installer and its log live — outside the folder being replaced."""
    return Path(tempfile.gettempdir()) / "OSRSToolkitUpdate"


def application_directory() -> Path:
    """The folder the running build was started from."""
    return Path(sys.executable).parent


def find_install(app_directory: Path | None = None) -> InstallLocation | None:
    """The registered installation this running copy belongs to, if it is one.

    A portable copy isn't an installation — nothing registered it or knows how to remove it.
    Compares this folder against the one Setup recorded, rather than checking for "Program
    Files" in the path (a portable copy can sit there too).
    """
    if sys.platform != "win32":
        return None
    import winreg

    directory = (app_directory or application_directory()).resolve()
    # Machine-wide first: both registrations can exist at once, but only the one we're
    # actually running from matters.
    for root, all_users in (
        (winreg.HKEY_LOCAL_MACHINE, True),
        (winreg.HKEY_CURRENT_USER, False),
    ):
        try:
            with winreg.OpenKey(root, _UNINSTALL_KEY) as key:
                location = str(winreg.QueryValueEx(key, "InstallLocation")[0]).strip()
        except OSError:
            continue
        if not location:
            continue
        try:
            registered = Path(location).resolve()
        except OSError:
            continue
        if registered == directory:
            return InstallLocation(directory=directory, all_users=all_users)
    return None


def silent_install_arguments(installer: Path, install: InstallLocation) -> list[str]:
    """The command line that replaces an installed copy without showing a wizard.

    ``/DIR`` plus the privilege switch keep the update in the same location — without them,
    an unelevated update of a machine-wide install would land in the user's folder instead,
    leaving two installations behind.
    """
    return [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCANCEL",
        # Read by packaging/installer.iss's [Run] section: relaunch the app once files are
        # in place.
        "/RELAUNCH=1",
        "/ALLUSERS" if install.all_users else "/CURRENTUSER",
        f"/DIR={install.directory}",
        # The app is gone by the time anything can go wrong, so this log is the only
        # record of a failed update.
        f"/LOG={update_directory() / 'install.log'}",
    ]


def start_installer(path: Path, install: InstallLocation | None = None) -> None:
    """Hand the update to the setup program and get out of its way.

    An installed copy is replaced in place with no wizard — the app already asked whether
    to update. A portable copy still gets the wizard, since it's being offered an install
    it doesn't have yet.

    Detached: what it's about to overwrite includes this process's own executable.
    """
    arguments = silent_install_arguments(path, install) if install else [str(path)]
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(arguments, close_fds=True, creationflags=creation_flags)
