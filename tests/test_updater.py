from __future__ import annotations

import hashlib
import io
import json
import winreg
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self
from unittest.mock import patch

import pytest

from osrs_toolkit.updater import (
    InstallLocation,
    ReleaseInfo,
    download_installer,
    fetch_latest_release,
    find_install,
    is_newer_version,
    silent_install_arguments,
    start_installer,
    version_tuple,
)


class FakeResponse(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_version_comparison() -> None:
    assert version_tuple("v1.2.3") == (1, 2, 3)
    assert is_newer_version("1.2.0", "1.1.9")
    assert not is_newer_version("1.1.0", "1.1.0")
    with pytest.raises(ValueError):
        version_tuple("latest")


def test_latest_release_selects_setup_asset() -> None:
    payload = {
        "tag_name": "v1.2.0",
        "html_url": "https://example.test/release",
        "assets": [
            {"name": "portable.zip", "browser_download_url": "https://example.test/zip"},
            {
                "name": "OSRS-Toolkit-Setup-1.2.0.exe",
                "browser_download_url": "https://example.test/setup",
                "digest": "sha256:abc123",
            },
        ],
    }
    with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(payload).encode())):
        release = fetch_latest_release()
    assert release.version == "1.2.0"
    assert release.installer_name == "OSRS-Toolkit-Setup-1.2.0.exe"
    assert release.installer_digest == "sha256:abc123"


def test_latest_release_requires_digest() -> None:
    payload = {
        "tag_name": "v1.2.0",
        "html_url": "https://example.test/release",
        "assets": [
            {
                "name": "OSRS-Toolkit-Setup-1.2.0.exe",
                "browser_download_url": "https://example.test/setup",
            }
        ],
    }
    with (
        patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(payload).encode())),
        pytest.raises(RuntimeError, match="SHA-256"),
    ):
        fetch_latest_release()


def test_download_verifies_digest(tmp_path: Path) -> None:
    content = b"installer-content"
    release = ReleaseInfo(
        version="1.2.0",
        page_url="https://example.test/release",
        installer_name="setup.exe",
        installer_url="https://example.test/setup",
        installer_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )
    progress: list[int] = []
    with (
        patch("urllib.request.urlopen", return_value=FakeResponse(content)),
        patch("tempfile.gettempdir", return_value=str(tmp_path)),
    ):
        path = download_installer(release, progress.append)
    assert path.read_bytes() == content
    assert progress[-1] == 100


def test_download_survives_a_transient_lock_on_finalize(tmp_path: Path) -> None:
    """Regression: a real-world WinError 5 from antivirus briefly holding the file open.

    The rename must succeed once the lock clears, without the whole download failing.
    """
    content = b"installer-content"
    release = ReleaseInfo(
        version="1.2.0",
        page_url="https://example.test/release",
        installer_name="setup.exe",
        installer_url="https://example.test/setup",
        installer_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )
    real_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self: Path, target: Path) -> Path:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    with (
        patch("urllib.request.urlopen", return_value=FakeResponse(content)),
        patch("tempfile.gettempdir", return_value=str(tmp_path)),
        patch("osrs_toolkit.updater.time.sleep"),
        patch.object(Path, "replace", flaky_replace),
    ):
        path = download_installer(release)
    assert path.read_bytes() == content
    assert attempts["count"] == 3


def test_download_reports_a_persistent_lock_clearly(tmp_path: Path) -> None:
    """A destination locked by something else must fail with an actionable message,
    not the raw WinError text, and must not leave the .part file behind."""
    content = b"installer-content"
    release = ReleaseInfo(
        version="1.2.0",
        page_url="https://example.test/release",
        installer_name="setup.exe",
        installer_url="https://example.test/setup",
        installer_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )

    def always_locked(self: Path, target: Path) -> Path:
        raise PermissionError(5, "Access is denied")

    with (
        patch("urllib.request.urlopen", return_value=FakeResponse(content)),
        patch("tempfile.gettempdir", return_value=str(tmp_path)),
        patch("osrs_toolkit.updater.time.sleep"),
        patch.object(Path, "replace", always_locked),
        pytest.raises(RuntimeError, match="Close any open installer windows"),
    ):
        download_installer(release)
    assert not (tmp_path / "OSRSToolkitUpdate" / "setup.exe.part").exists()


def test_download_rejects_bad_digest(tmp_path: Path) -> None:
    release = ReleaseInfo(
        version="1.2.0",
        page_url="https://example.test/release",
        installer_name="setup.exe",
        installer_url="https://example.test/setup",
        installer_digest="sha256:" + "0" * 64,
    )
    with (
        patch("urllib.request.urlopen", return_value=FakeResponse(b"tampered")),
        patch("tempfile.gettempdir", return_value=str(tmp_path)),
        pytest.raises(RuntimeError, match="security check"),
    ):
        download_installer(release)


class FakeKey:
    """The one value find_install reads out of the uninstall entry."""

    def __init__(self, location: str) -> None:
        self.location = location

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@contextmanager
def registered_installs(locations: dict[int, str]) -> Iterator[None]:
    """Stand in for the uninstall entry Inno Setup writes, in the hives given."""

    def open_key(root: int, _path: str) -> FakeKey:
        if root not in locations:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return FakeKey(locations[root])

    def query_value(key: FakeKey, name: str) -> tuple[str, int]:
        assert name == "InstallLocation"
        return key.location, winreg.REG_SZ

    with patch("winreg.OpenKey", open_key), patch("winreg.QueryValueEx", query_value):
        yield


def test_a_portable_copy_has_no_registered_install(tmp_path: Path) -> None:
    with registered_installs({}):
        assert find_install(tmp_path) is None


def test_an_installed_copy_is_matched_by_its_own_folder(tmp_path: Path) -> None:
    with registered_installs({winreg.HKEY_CURRENT_USER: str(tmp_path)}):
        install = find_install(tmp_path)
    assert install == InstallLocation(directory=tmp_path.resolve(), all_users=False)


def test_a_machine_wide_install_is_recognised_as_one(tmp_path: Path) -> None:
    with registered_installs({winreg.HKEY_LOCAL_MACHINE: str(tmp_path)}):
        install = find_install(tmp_path)
    assert install is not None
    assert install.all_users


def test_running_the_portable_copy_alongside_an_installed_one_stays_portable(
    tmp_path: Path,
) -> None:
    """Owning an installed copy does not make the portable one an installed copy.

    Both can sit on the same machine, and the registered folder is what tells them apart —
    without which a portable folder would be silently replaced by an installer that
    believes it is updating something else.
    """
    installed = tmp_path / "installed"
    portable = tmp_path / "portable"
    installed.mkdir()
    portable.mkdir()
    with registered_installs({winreg.HKEY_CURRENT_USER: str(installed)}):
        assert find_install(portable) is None


def test_an_empty_install_location_is_not_a_match(tmp_path: Path) -> None:
    """A blank InstallLocation resolves to the working directory, which would match by
    accident from the wrong folder."""
    with registered_installs({winreg.HKEY_CURRENT_USER: "   "}):
        assert find_install(tmp_path) is None


def test_silent_arguments_keep_a_machine_wide_update_where_it_is(tmp_path: Path) -> None:
    install = InstallLocation(directory=tmp_path / "OSRS Toolkit", all_users=True)
    arguments = silent_install_arguments(tmp_path / "setup.exe", install)
    assert arguments[0] == str(tmp_path / "setup.exe")
    assert "/VERYSILENT" in arguments
    assert "/ALLUSERS" in arguments
    assert "/CURRENTUSER" not in arguments
    assert f"/DIR={install.directory}" in arguments
    assert "/RELAUNCH=1" in arguments


def test_a_per_user_install_is_not_sent_looking_for_elevation(tmp_path: Path) -> None:
    arguments = silent_install_arguments(
        tmp_path / "setup.exe", InstallLocation(directory=tmp_path, all_users=False)
    )
    assert "/CURRENTUSER" in arguments
    assert "/ALLUSERS" not in arguments


def test_a_portable_update_still_opens_the_wizard(tmp_path: Path) -> None:
    with patch("osrs_toolkit.updater.subprocess.Popen") as popen:
        start_installer(tmp_path / "setup.exe", None)
    assert popen.call_args.args[0] == [str(tmp_path / "setup.exe")]


def test_an_installed_update_runs_without_a_wizard(tmp_path: Path) -> None:
    install = InstallLocation(directory=tmp_path, all_users=False)
    with patch("osrs_toolkit.updater.subprocess.Popen") as popen:
        start_installer(tmp_path / "setup.exe", install)
    assert "/VERYSILENT" in popen.call_args.args[0]
