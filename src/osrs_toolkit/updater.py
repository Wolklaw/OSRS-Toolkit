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
USER_AGENT = (
    f"OSRS-Toolkit-Updater/{__version__} (+https://github.com/Wolklaw/OSRS-Toolkit)"
)
_FINALIZE_ATTEMPTS = 5
_FINALIZE_RETRY_SECONDS = 0.4

# Inno Setup registers an uninstall entry under the AppId in packaging/installer.iss with
# "_is1" appended. Changing the AppId there without changing it here would leave every
# installed copy believing it is portable, quietly demoting in-place updates back to the
# wizard, so the two are a pair.
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


def update_directory() -> Path:
    """Where the downloaded installer and its log live — outside the folder being replaced."""
    return Path(tempfile.gettempdir()) / "OSRSToolkitUpdate"


def application_directory() -> Path:
    """The folder the running build was started from."""
    return Path(sys.executable).parent


def find_install(app_directory: Path | None = None) -> InstallLocation | None:
    """The registered installation this running copy belongs to, if it is one.

    A portable copy is not an installation: nothing registered it, nothing knows how to
    remove it, and rewriting its folder from underneath it is not this program's business.
    Setup records the folder it installed into, so "is the code now running the installed
    copy?" is answered by comparing that folder against this one — rather than by looking
    for Program Files somewhere in the path, which a portable folder is free to sit inside
    too, and which says nothing about whether there is an installation to update.
    """
    if sys.platform != "win32":
        return None
    import winreg

    directory = (app_directory or application_directory()).resolve()
    # Machine-wide first: both registrations can exist at once, and the one this
    # executable is actually running from is the one that answers the question.
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

    ``/DIR`` and the privilege switch together are what keep an update where it already
    is. Left to its defaults the setup program installs wherever it is entitled to, so a
    machine-wide copy updated by an unelevated app would land in the user's own folder and
    leave the original sitting there — two installations, one of them stale, and a Start
    Menu entry pointing at whichever was written last.
    """
    return [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCANCEL",
        # Read by the [Run] section of packaging/installer.iss: start the app again once
        # the files are in place, because this update closed it in order to replace them.
        "/RELAUNCH=1",
        "/ALLUSERS" if install.all_users else "/CURRENTUSER",
        f"/DIR={install.directory}",
        # The app is gone by the time anything can go wrong, so this log is the only
        # account of a failed update that anyone could later be asked for.
        f"/LOG={update_directory() / 'install.log'}",
    ]


def start_installer(path: Path, install: InstallLocation | None = None) -> None:
    """Hand the update to the setup program and get out of its way.

    An installed copy is replaced in place and restarted with no wizard: the app has
    already asked whether to update, and putting the same question in a second window is
    not consent, it is another dialog. A portable copy still gets the wizard, because for
    it the wizard asks something real — it is being offered an installed copy it does not
    have, rather than a newer version of one it does.

    Detached, because what it is about to overwrite includes this process's own executable.
    """
    arguments = silent_install_arguments(path, install) if install else [str(path)]
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(arguments, close_fds=True, creationflags=creation_flags)
