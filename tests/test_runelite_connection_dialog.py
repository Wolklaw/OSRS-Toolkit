from __future__ import annotations

from osrs_toolkit.app import RuneLiteConnectionDialog
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter
from osrs_toolkit.sync_source import RuneLiteConnectionStatus
from osrs_toolkit.web_source import ToolkitWebClient, WebAppSource


def _local_importer(tmp_path) -> RuneLiteSyncImporter:
    return RuneLiteSyncImporter(tmp_path / "runelite-sync")


def _website_importer() -> RuneLiteSyncImporter:
    return RuneLiteSyncImporter(source=WebAppSource(ToolkitWebClient("https://x.test", "tok")))


def test_the_local_bridge_still_claims_only_local_files(qt_app, tmp_path):
    dialog = RuneLiteConnectionDialog(_local_importer(tmp_path))
    try:
        assert "local files only" in dialog.privacy.text()
        assert "runescope.app" not in dialog.privacy.text()
    finally:
        dialog.deleteLater()


def test_a_website_connected_install_says_where_data_actually_goes(qt_app):
    dialog = RuneLiteConnectionDialog(_website_importer())
    try:
        assert "runescope.app" in dialog.privacy.text()
        assert "local files only" not in dialog.privacy.text()
    finally:
        dialog.deleteLater()


def test_the_folder_button_is_hidden_when_there_is_no_folder(qt_app):
    dialog = RuneLiteConnectionDialog(_website_importer())
    try:
        assert dialog.importer.sync_root is None
        assert dialog.folder_button.isHidden() is True
    finally:
        dialog.deleteLater()


def test_the_folder_button_still_works_for_the_local_bridge(qt_app, tmp_path):
    dialog = RuneLiteConnectionDialog(_local_importer(tmp_path))
    try:
        assert dialog.folder_button.isHidden() is False
    finally:
        dialog.deleteLater()


def test_an_unreachable_website_is_not_blamed_on_the_plugin(qt_app, monkeypatch):
    importer = _website_importer()
    monkeypatch.setattr(
        importer,
        "connection_status",
        lambda: RuneLiteConnectionStatus(detected=True, active=False, source_reachable=False),
    )
    dialog = RuneLiteConnectionDialog(importer)
    try:
        assert "Cannot reach the website" in dialog.status.text()
        assert "RuneLite is not currently active" not in dialog.status.text()
    finally:
        dialog.deleteLater()


def test_no_desktop_token_says_so_instead_of_blaming_the_plugin(qt_app):
    importer = RuneLiteSyncImporter(source=WebAppSource(ToolkitWebClient("https://x.test", "")))
    dialog = RuneLiteConnectionDialog(importer)
    try:
        assert "No desktop access token" in dialog.status.text()
        assert "Install and enable the RuneLite plugin" not in dialog.status.text()
    finally:
        dialog.deleteLater()


def test_a_configured_but_silent_website_still_blames_the_plugin_not_missing_setup(
    qt_app, monkeypatch
):
    importer = _website_importer()
    monkeypatch.setattr(
        importer,
        "connection_status",
        lambda: RuneLiteConnectionStatus(detected=False, active=False, source_reachable=True),
    )
    dialog = RuneLiteConnectionDialog(importer)
    try:
        assert "No desktop access token" not in dialog.status.text()
        assert "Not connected yet" in dialog.status.text()
    finally:
        dialog.deleteLater()
