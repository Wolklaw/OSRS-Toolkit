from __future__ import annotations

import os

from PySide6.QtCore import QByteArray

from osrs_toolkit import app as app_module
from osrs_toolkit.app import WINDOW_GEOMETRY_KEY, MainWindow

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_closing_the_window_remembers_where_it_was(window: MainWindow) -> None:
    window.setGeometry(120, 90, 1100, 700)

    window.close()

    saved = app_module.QSettings().value(WINDOW_GEOMETRY_KEY)
    assert isinstance(saved, QByteArray)
    assert not saved.isEmpty()


def test_the_next_launch_opens_where_the_last_one_closed(window: MainWindow) -> None:
    window.setGeometry(140, 60, 1_100, 700)
    window.close()
    window.setGeometry(300, 300, 1_050, 680)
    moved_away = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() != moved_away
    assert window.geometry().y() == 60
    assert window.geometry().height() == 700


def test_a_first_launch_keeps_the_default_size(window: MainWindow) -> None:
    app_module.QSettings().remove(WINDOW_GEOMETRY_KEY)
    before = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() == before


def test_a_saved_value_that_is_not_a_geometry_is_ignored(window: MainWindow) -> None:
    app_module.QSettings().setValue(WINDOW_GEOMETRY_KEY, "not a geometry")
    before = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() == before


def test_a_window_off_every_screen_is_not_treated_as_reachable(window: MainWindow) -> None:
    window.setGeometry(6_000, 6_000, 1_100, 700)
    assert window._is_on_an_attached_screen() is False

    window.setGeometry(0, 0, 1_100, 700)
    assert window._is_on_an_attached_screen() is True


def test_a_window_barely_peeking_onto_a_screen_does_not_count(window: MainWindow) -> None:
    window.setGeometry(-1_060, 0, 1_100, 700)

    assert window._is_on_an_attached_screen() is False
