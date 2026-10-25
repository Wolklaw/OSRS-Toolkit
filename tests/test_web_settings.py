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
from osrs_toolkit.web_source import WebAppSource


class StubSettings:
    _values: ClassVar[dict[str, object]] = {}

    def value(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self._values[key] = value

    def remove(self, key: str) -> None:
        self._values.pop(key, None)


def _mirror(window) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    window._mirror_journal()
    deadline = time.monotonic() + 5
    while window._mirror_thread is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


@pytest.fixture
def settings(monkeypatch):
    StubSettings._values.clear()
    monkeypatch.setattr("osrs_toolkit.app.QSettings", StubSettings)
    yield StubSettings()
    StubSettings._values.clear()


def test_without_a_token_the_source_is_still_the_website_not_the_folder(settings):
    importer = build_sync_importer()
    assert isinstance(importer.source, WebAppSource)
    assert importer.configured is False


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
    assert configured_web_client().base_url == "https://toolkit.example.test"


def test_an_address_with_no_token_is_not_configured(settings):
    settings.setValue(WEB_BASE_URL_KEY, "https://runescope.app")
    assert configured_web_client().configured is False
    importer = build_sync_importer()
    assert isinstance(importer.source, WebAppSource)
    assert importer.configured is False


def test_saving_a_token_rebuilds_the_importer(window):
    assert isinstance(window._sync_importer.source, WebAppSource)
    assert window._sync_importer.configured is False

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")

    assert isinstance(window._sync_importer.source, WebAppSource)
    assert window._sync_importer.configured is True
    assert app_module.QSettings().value(WEB_TOKEN_KEY) == "a-desktop-token"


def test_clearing_the_token_leaves_the_source_unconfigured_not_reading_the_folder(window):
    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    assert window._sync_importer.configured is True

    window._save_website_settings(DEFAULT_BASE_URL, "")

    assert isinstance(window._sync_importer.source, WebAppSource)
    assert window._sync_importer.configured is False


def test_saving_the_same_values_leaves_the_importer_alone(window):
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
        assert dialog.web_token_field.echoMode() == dialog.web_token_field.EchoMode.Password
    finally:
        dialog.deleteLater()


def test_no_token_means_the_mirror_never_reaches_out(window):
    calls = []
    window._journal_mirror.client.get = lambda path, **params: calls.append(path)

    _mirror(window)

    assert calls == []


def test_saving_a_token_starts_the_mirror_over(window):
    before = window._journal_mirror
    window._journal_mirror.remote_version = "2026-08-22T00:00:00+00:00"

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")

    assert window._journal_mirror is not before
    assert window._journal_mirror.remote_version is None
    assert window._journal_mirror.client.token == "a-desktop-token"


def test_a_failing_sync_does_not_break_the_ui_loop(window):
    def explode(*_args, **_kwargs):
        raise RuntimeError("the website fell over")

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    window._journal_mirror.sync = explode

    _mirror(window)

    assert "journal sync paused" in window._last_mirror_message


def test_the_interval_backs_off_when_the_window_is_not_in_front(window):
    from osrs_toolkit.app import MIRROR_BACKGROUND_INTERVAL_MS, MIRROR_INTERVAL_MS

    window._apply_mirror_interval(True)
    assert window._mirror_timer.interval() == MIRROR_INTERVAL_MS

    window._apply_mirror_interval(False)
    assert window._mirror_timer.interval() == MIRROR_BACKGROUND_INTERVAL_MS


def test_a_sync_that_changed_something_redraws_the_journal(window):
    from osrs_toolkit.journal_mirror import MirrorResult

    redrawn = []
    window._render_journal = lambda: redrawn.append("journal")
    window._render_performance = lambda: redrawn.append("performance")
    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    window._journal_mirror.sync = lambda: MirrorResult(reached=True, pulled=3)

    _mirror(window)

    assert redrawn == ["journal", "performance"]


def test_a_quiet_sync_redraws_nothing(window):
    from osrs_toolkit.journal_mirror import MirrorResult

    redrawn = []
    window._render_journal = lambda: redrawn.append("journal")
    window._render_performance = lambda: redrawn.append("performance")
    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    window._journal_mirror.sync = lambda: MirrorResult(reached=True, checked_only=True)

    _mirror(window)

    assert redrawn == []


def test_a_sync_shows_up_beside_the_connection_status(window):
    from osrs_toolkit.journal_mirror import MirrorResult

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    window._journal_mirror.sync = lambda: MirrorResult(reached=True, pulled=3, pushed=1)

    _mirror(window)

    assert "Synced 3 in, 1 out" in window.runelite_status.text()


def test_a_quiet_pass_leaves_the_line_clean(window):
    from osrs_toolkit.journal_mirror import MirrorResult

    window._save_website_settings(DEFAULT_BASE_URL, "a-desktop-token")
    window._journal_mirror.sync = lambda: MirrorResult(reached=True, checked_only=True)

    _mirror(window)

    assert "Synced" not in window.runelite_status.text()


def test_an_unreachable_website_is_not_reported_as_the_plugin_being_offline(window):
    from osrs_toolkit.sync_source import RuneLiteConnectionStatus

    window._sync_importer.connection_status = lambda: RuneLiteConnectionStatus(
        detected=True, active=False, source_reachable=False
    )

    window._update_runelite_status()

    text = window.runelite_status.text()
    assert "Cannot reach the website" in text
    assert "RuneLite offline" not in text
    assert window.runelite_button.text() == "Website unreachable"


def test_the_unreachable_reason_is_shown_when_known(window):
    from osrs_toolkit.sync_source import RuneLiteConnectionStatus

    window._sync_importer.connection_status = lambda: RuneLiteConnectionStatus(
        detected=True, active=False, source_reachable=False, last_error="timed out"
    )

    window._update_runelite_status()

    assert "(timed out)" in window.runelite_status.text()


def test_the_local_folder_is_never_called_unreachable(window):
    from osrs_toolkit.sync_source import RuneLiteConnectionStatus

    window._sync_importer.connection_status = lambda: RuneLiteConnectionStatus(
        detected=True, active=False
    )

    window._update_runelite_status()

    assert "RuneLite offline" in window.runelite_status.text()


def test_the_unreachable_line_does_not_say_it_twice(window):
    from osrs_toolkit.sync_source import RuneLiteConnectionStatus

    window._last_mirror_message = " • Could not reach the website"
    window._sync_importer.connection_status = lambda: RuneLiteConnectionStatus(
        detected=True, active=False, source_reachable=False
    )

    window._update_runelite_status()

    assert window.runelite_status.text().count("each the website") == 1
