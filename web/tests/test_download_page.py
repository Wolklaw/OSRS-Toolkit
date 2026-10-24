from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from toolkit_web.releases import Download, Release, ReleaseCache, _parse

LATEST = Release(
    version="1.2.1",
    published_on="2026-08-23",
    installer=Download(
        name="OSRS-Toolkit-Setup-1.2.1.exe",
        url="https://example.invalid/OSRS-Toolkit-Setup-1.2.1.exe",
        size_mb=25.2,
    ),
    portable=Download(
        name="OSRS-Toolkit-Portable-1.2.1.zip",
        url="https://example.invalid/OSRS-Toolkit-Portable-1.2.1.zip",
        size_mb=33.8,
    ),
)


def test_the_download_page_offers_both_builds_of_the_current_release(client: TestClient):
    with patch("toolkit_web.releases.fetch_latest", return_value=LATEST):
        page = client.get("/download").text

    assert "1.2.1" in page
    assert LATEST.installer.url in page
    assert LATEST.portable.url in page


def test_the_download_page_reads_signed_out(client: TestClient):
    with patch("toolkit_web.releases.fetch_latest", return_value=LATEST):
        response = client.get("/download")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_github_being_unreachable_still_leaves_somewhere_to_download_from(client: TestClient):
    with patch("toolkit_web.releases.fetch_latest", return_value=None):
        response = client.get("/download")

    assert response.status_code == 200
    assert "https://github.com/Wolklaw/OSRS-Toolkit/releases" in response.text


def test_the_site_links_to_the_desktop_app_from_the_landing_page(client: TestClient):
    assert "/download" in client.get("/").text


def test_a_signed_in_page_links_to_the_desktop_app_too(signed_in: TestClient):
    assert "/download" in signed_in.get("/").text


def test_the_release_is_only_fetched_once_inside_its_refresh_window():
    with patch("toolkit_web.releases.fetch_latest", return_value=LATEST) as fetch:
        cache = ReleaseCache()
        assert cache.latest() == LATEST
        assert cache.latest() == LATEST

    fetch.assert_called_once()


def test_a_failed_refresh_keeps_whatever_was_last_fetched():
    cache = ReleaseCache()
    with patch("toolkit_web.releases.fetch_latest", return_value=LATEST):
        cache.latest()

    with patch("toolkit_web.releases.fetch_latest", return_value=None):
        assert cache.latest() == LATEST


def test_assets_are_picked_by_file_type_rather_than_by_name():
    release = _parse(
        {
            "tag_name": "v9.9.9",
            "published_at": "2026-09-01T10:00:00Z",
            "assets": [
                {"name": "checksums.txt", "browser_download_url": "https://x/checksums.txt"},
                {"name": "renamed.zip", "browser_download_url": "https://x/z.zip", "size": 1000000},
                {"name": "renamed.exe", "browser_download_url": "https://x/e.exe", "size": 2000000},
            ],
        }
    )

    assert release.version == "9.9.9"
    assert release.published_on == "2026-09-01"
    assert release.installer.url == "https://x/e.exe"
    assert release.portable.url == "https://x/z.zip"


def test_a_release_with_no_assets_still_parses():
    release = _parse({"tag_name": "v1.0.0", "assets": []})

    assert release.version == "1.0.0"
    assert release.installer is None
    assert release.portable is None


def test_installing_this_site_is_offered_before_the_windows_installer(client: TestClient):
    with patch("toolkit_web.releases.fetch_latest", return_value=LATEST):
        page = client.get("/download").text

    assert page.index("data-install-cta") < page.index(LATEST.installer.url)
