"""Shared fixtures for tests that drive real Qt widgets.

These run the actual ``MainWindow`` offscreen against a throwaway database, so a test can
assert on what the app would really put on screen rather than on a reimplementation of it.
"""

from __future__ import annotations

import functools
import gc
import os
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import MainWindow
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter


class StubSettings:
    """QSettings without touching the real user registry/profile.

    App code constructs ``QSettings()`` fresh at each call site, so the backing store has
    to live on the class for values to persist the way real settings do.
    """

    _values: ClassVar[dict[str, object]] = {}

    def value(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self._values[key] = value

    def remove(self, key: str) -> None:
        self._values.pop(key, None)


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app: QApplication, tmp_path, monkeypatch) -> MainWindow:
    """A real MainWindow on a throwaway database, with network and sync disabled."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    StubSettings._values.clear()
    monkeypatch.setattr("osrs_toolkit.app.QSettings", StubSettings)
    monkeypatch.setattr(MainWindow, "load_market", lambda self: None)
    monkeypatch.setattr(MainWindow, "_import_runelite_events", lambda self: None)
    # MainWindow.__init__ schedules this for 600ms after start-up, and it fires a real
    # request to the GitHub releases API on a QThread of its own. Left alone it makes the
    # test run depend on the network, and — because a check started by one test comes due
    # during a later one — a request still in flight when its window closes outruns
    # closeEvent's wait and takes the whole process down with it when Qt destroys a QThread
    # that is still running. test_startup_notices.py calls the notice methods directly, so
    # nothing here loses coverage.
    monkeypatch.setattr(MainWindow, "_run_startup_notices", lambda self: None)
    # MainWindow.__init__ builds a RuneLiteSyncImporter() with no sync_root, which defaults
    # to the real ~/.runelite/osrs-toolkit — without this, tests read (and, if a test calls
    # the real _import_runelite_events, would delete/move) the developer's actual live
    # RuneLite sync data instead of a throwaway one.
    monkeypatch.setattr(
        "osrs_toolkit.app.RuneLiteSyncImporter",
        functools.partial(RuneLiteSyncImporter, tmp_path / "runelite-sync"),
    )
    created = MainWindow()
    yield created
    # MainWindow starts three QTimers in __init__ and nothing ever stops them on close —
    # fine for a real app process that's about to exit, but qt_app is session-scoped here,
    # so a leaked timer keeps firing into the shared event loop for the rest of the test
    # run. Stopping them isn't enough on its own either: the QMainWindow and its child
    # widgets still linger until something actually forces them out, and across hundreds
    # of these across a full run that backlog eventually makes some later test's own
    # processEvents()/QThread.wait() stall working through it. Close, stop the timers,
    # drop the only Python reference, then force the C++ objects out and flush Qt's
    # deleteLater() queue before the next test gets a turn.
    created._cash_debounce.stop()
    created._sync_timer.stop()
    created._market_timer.stop()
    created.close()
    created.deleteLater()
    del created
    gc.collect()
    qt_app.processEvents()
