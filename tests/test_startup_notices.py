"""The start-up "What's new" and update-available windows."""

from __future__ import annotations

import osrs_toolkit.app as app_module
from osrs_toolkit import __version__
from osrs_toolkit.app import LAST_SEEN_VERSION_KEY, SKIPPED_VERSION_KEY, MainWindow
from osrs_toolkit.release_notes import ReleaseNotes, ReleaseSection
from osrs_toolkit.updater import ReleaseInfo

NOTES = ReleaseNotes(
    version=__version__,
    date="2026-08-15",
    sections=(ReleaseSection("Fixed", ("Something.",)),),
)
EARLIER = ReleaseNotes(
    version="0.0.2",
    date="2026-08-14",
    sections=(ReleaseSection("Fixed", ("Something before that.",)),),
)


def _release(version: str) -> ReleaseInfo:
    return ReleaseInfo(
        version=version,
        page_url="https://example.test/release",
        installer_name="setup.exe",
        installer_url="https://example.test/setup",
        installer_digest="sha256:" + "0" * 64,
    )


class RecordingDialog:
    """Stands in for a modal dialog, counting the times it would have opened and keeping
    what it was asked to show."""

    opened: int = 0
    shown: object = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).shown = args[0] if args else None

    def exec(self) -> int:
        type(self).opened += 1
        return 0


def _patch_dialogs(monkeypatch) -> tuple[type[RecordingDialog], type[RecordingDialog]]:
    whats_new = type("WhatsNew", (RecordingDialog,), {"opened": 0, "shown": None})
    update = type("UpdateOffer", (RecordingDialog,), {"opened": 0, "shown": None})
    monkeypatch.setattr(app_module, "WhatsNewDialog", whats_new)
    monkeypatch.setattr(app_module, "UpdateAvailableDialog", update)
    return whats_new, update


def _patch_notes(monkeypatch, notes: list[ReleaseNotes]) -> list[dict[str, object]]:
    """Stand in for the bundled changelog, recording how it was asked for."""
    asked: list[dict[str, object]] = []

    def read(**kwargs: object) -> list[ReleaseNotes]:
        asked.append(kwargs)
        return notes

    monkeypatch.setattr(app_module, "current_release_notes", read)
    return asked


def test_whats_new_shows_once_per_version(window: MainWindow, monkeypatch) -> None:
    whats_new, _update = _patch_dialogs(monkeypatch)
    _patch_notes(monkeypatch, [NOTES])

    window._show_whats_new_if_updated()
    assert whats_new.opened == 1
    assert app_module.QSettings().value(LAST_SEEN_VERSION_KEY) == __version__

    window._show_whats_new_if_updated()
    assert whats_new.opened == 1, "the notes reopened for a version already seen"


def test_whats_new_shows_again_after_an_upgrade(window: MainWindow, monkeypatch) -> None:
    whats_new, _update = _patch_dialogs(monkeypatch)
    asked = _patch_notes(monkeypatch, [NOTES, EARLIER])
    app_module.QSettings().setValue(LAST_SEEN_VERSION_KEY, "0.0.1")

    window._show_whats_new_if_updated()

    assert whats_new.opened == 1
    assert asked == [{"since": "0.0.1", "limit": 5}], "the versions missed were not asked for"
    assert whats_new.shown == [NOTES, EARLIER], "only the newest release was shown"


def test_a_first_run_announces_itself_without_a_history(
    window: MainWindow, monkeypatch
) -> None:
    """Nothing was missed on a fresh install, so it opens on this version alone rather
    than on releases the machine never ran."""
    _whats_new, _update = _patch_dialogs(monkeypatch)
    asked = _patch_notes(monkeypatch, [NOTES])

    window._show_whats_new_if_updated()

    assert asked == [{"since": "", "limit": 1}]


def test_whats_new_stays_closed_without_notes(window: MainWindow, monkeypatch) -> None:
    whats_new, _update = _patch_dialogs(monkeypatch)
    _patch_notes(monkeypatch, [])

    window._show_whats_new_if_updated()
    assert whats_new.opened == 0
    # The version still counts as seen, so a build without notes does not retry forever.
    assert app_module.QSettings().value(LAST_SEEN_VERSION_KEY) == __version__


def test_update_window_opens_only_for_a_newer_release(window: MainWindow, monkeypatch) -> None:
    _whats_new, update = _patch_dialogs(monkeypatch)

    window._startup_update_check_finished(_release(__version__))
    assert update.opened == 0, "an up-to-date app interrupted start-up"

    window._startup_update_check_finished(_release("99.0.0"))
    assert update.opened == 1


def test_skipped_version_does_not_reopen(window: MainWindow, monkeypatch) -> None:
    _whats_new, update = _patch_dialogs(monkeypatch)
    app_module.QSettings().setValue(SKIPPED_VERSION_KEY, "99.0.0")

    window._startup_update_check_finished(_release("99.0.0"))
    assert update.opened == 0

    # A different release is still offered.
    window._startup_update_check_finished(_release("99.1.0"))
    assert update.opened == 1
