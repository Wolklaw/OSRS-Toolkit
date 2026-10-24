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
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    StubSettings._values.clear()
    monkeypatch.setattr("osrs_toolkit.app.QSettings", StubSettings)
    monkeypatch.setattr(MainWindow, "load_market", lambda self: None)
    monkeypatch.setattr(MainWindow, "_import_runelite_events", lambda self: None)
    monkeypatch.setattr(MainWindow, "_run_startup_notices", lambda self: None)
    monkeypatch.setattr(
        "osrs_toolkit.app.RuneLiteSyncImporter",
        functools.partial(RuneLiteSyncImporter, tmp_path / "runelite-sync"),
    )
    created = MainWindow()
    yield created
    created._cash_debounce.stop()
    created._sync_timer.stop()
    created._market_timer.stop()
    created.close()
    created.deleteLater()
    del created
    gc.collect()
    qt_app.processEvents()
