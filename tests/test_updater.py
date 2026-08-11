from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Self
from unittest.mock import patch

import pytest

from osrs_toolkit.updater import (
    ReleaseInfo,
    download_installer,
    fetch_latest_release,
    is_newer_version,
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
