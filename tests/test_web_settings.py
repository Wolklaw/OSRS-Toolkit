"""Which source the app reads from, and how a token entered in Settings changes it.

The Plugin Hub refused a plugin that feeds an app on the same machine, so a fresh install no
longer reads the ``.runelite`` folder -- it reads the website. The folder stays reachable for
somebody still running an older plugin, and these pin down which of the two you get.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from osrs_toolkit import app as app_module
from osrs_toolkit.app import (
    DEFAULT_BASE_URL,
    WEB_BASE_URL_KEY,
    WEB_TOKEN_KEY,
    build_sync_importer,
    configured_web_client,
)
from osrs_toolkit.sync_source import LocalFileSource
from osrs_toolkit.web_source import WebAppSource


class StubSettings:
    """The same shape as conftest's, kept local because these tests are the ones that need
    settings without also needing a whole window built around them."""

    _values: ClassVar[dict[str, object]] = {}

    def value(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self._values[key] = value

    def remove(self, key: str) -> None:
        self._values.pop(key, None)


@pytest.fixture
def settings(monkeypatch):
    """Settings with nothing in them, for the tests that do not build a window.

    The ``window`` fixture already installs its own stub, so a test taking that one reads and
    writes through ``osrs_toolkit.app.QSettings()`` instead of asking for this.
    """
    StubSettings._values.clear()
    monkeypatch.setattr("osrs_toolkit.app.QSettings", StubSettings)
    yield StubSettings()
    StubSettings._values.clear()


def test_without_a_token_the_old_folder_is_still_read(settings):
    """Silently reading nothing would look exactly like the app being broken."""
    importer = build_sync_importer()
    assert isinstance(importer.source, LocalFileSource)


def test_a_token_switches_the_app_over_to_the_website(settings):
    settings.setValue(WEB_TOKEN_KEY, "a-desktop-token")
    importer = build_sync_importer()
    assert isinstance(importer.source, WebAppSource)
    assert importer.source.client.token == "a-desktop-token"


def test_the_address_defaults_to_the_real_site(settings):
    settings.setValue(WEB_TOKEN_KEY, "a-desktop-token")
    assert configured_web_client().base_url == DEFAULT_BASE_URL


def test_a_self_hosted_address_is_honoured(settings):
    settings.setValue(WEB_BASE_URL_KEY, "https://toolkit.example.test/")
    settings.setValue(WEB_TOKEN_KEY, "a-desktop-token")
    # The trailing slash is normalised away so paths do not end up doubled.
    assert configured_web_client().base_url == "https://toolkit.example.test"


def test_an_address_with_no_token_is_not_configured(settings):
    settings.setValue(WEB_BASE_URL_KEY, "https://runescope.app")
    assert configured_web_client().configured is False
    assert isinstance(build_sync_importer().source, LocalFileSource)


def test_saving_a_token_rebuilds_the_importer(window):
    """A token pasted into Settings has to take effect now, not at the next restart."""
    assert isinstance(window._sync_importer.source, LocalFileSource)

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")

    assert isinstance(window._sync_importer.source, WebAppSource)
    assert app_module.QSettings().value(WEB_TOKEN_KEY) == "a-desktop-token"


def test_clearing_the_token_goes_back_to_the_folder(window):
    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    assert isinstance(window._sync_importer.source, WebAppSource)

    window._save_website_settings(DEFAULT_BASE_URL, "")

    assert isinstance(window._sync_importer.source, LocalFileSource)


def test_saving_the_same_values_leaves_the_importer_alone(window):
    """Reopening Settings and pressing OK should not quietly drop a warmed-up source."""
    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    before = window._sync_importer

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")

    assert window._sync_importer is before


def test_the_settings_dialog_shows_what_is_saved(qt_app, tmp_path, settings):
    from osrs_toolkit.app import SettingsDialog

    settings.setValue(WEB_BASE_URL_KEY, "https://toolkit.example.test")
    settings.setValue(WEB_TOKEN_KEY, "a-desktop-token")
    client = configured_web_client()

    dialog = SettingsDialog("Dark", Path(tmp_path) / "journal.db", client.base_url, client.token)
    try:
        assert dialog.web_base_url_field.text() == "https://toolkit.example.test"
        assert dialog.web_token_field.text() == "a-desktop-token"
        # The token is a credential: it should not be readable over somebody's shoulder.
        assert dialog.web_token_field.echoMode() == dialog.web_token_field.EchoMode.Password
    finally:
        dialog.deleteLater()
