"""What the Connect RuneLite dialog tells somebody, and whether that is still true.

The privacy line here used to say "local files only, nothing sent anywhere" unconditionally.
Once a signed-in install started reading through the website instead of a folder on this PC,
that sentence became a false claim rather than a stale one — these pin the dialog to say the
right thing for whichever source is actually configured.
"""

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
    """This is the claim that was false before: reading through the website means trade and
    position data really does leave the PC, and the label has to say so honestly."""
    dialog = RuneLiteConnectionDialog(_website_importer())
    try:
        assert "runescope.app" in dialog.privacy.text()
        assert "local files only" not in dialog.privacy.text()
    finally:
        dialog.deleteLater()


def test_the_folder_button_is_hidden_when_there_is_no_folder(qt_app):
    """There is nothing for it to open when the source is the website — sync_root is None,
    and offering the button invited a broken QUrl.fromLocalFile("None").

    isHidden() rather than isVisible(): the dialog is never shown in these tests, and
    isVisible() answers False for every widget in an unshown window regardless of what
    setVisible() was actually called with -- it would pass here whether the fix existed or not.
    """
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
    """Detected-but-no-answer means the website could not be reached, not that RuneLite is
    offline -- telling somebody to check RuneLite here would send them to fix the wrong thing."""
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
    """An unconfigured website source and a plugin that was never installed answer
    ``connection_status()`` identically -- both report nothing detected. Without this, someone
    who is already running the plugin gets told to go install it, which sends them back to a
    settings panel that was never the problem."""
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
    """The new message is specifically about a missing token -- a configured source that
    simply has nothing detected yet should read as it always has. connection_status() is
    stubbed rather than left to hit the real network: an actually-unreachable test domain
    would report "cannot reach the website" instead, which is a different, correct message
    for a different case and would make this test pass for the wrong reason."""
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
