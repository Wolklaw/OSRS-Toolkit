"""The window is opened beside a game and alt-tabbed to all evening.

It used to come back at 1280x760 in the middle of the screen every launch, whatever the
player had arranged the evening before — the one piece of state a companion window has to
keep for the arrangement around it to be worth making.

The offscreen platform these run on reports a single 800x800 screen, which is narrower than
the window's own 1050px minimum, so Qt clamps the width of anything restored here. The
assertions stay on the parts a clamp leaves alone.

Settings are read through ``app_module.QSettings()`` — the `window` fixture's stand-in —
rather than by importing it from conftest, which pytest loads under its own module identity.
"""

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
    """The round trip that matters: what closeEvent saved is what __init__ restores.

    The saved height is one that fits inside the offscreen screen from the saved y, so the
    clamp has nothing to do vertically and the assertion is about the round trip.
    """
    window.setGeometry(140, 60, 1_100, 700)
    window.close()
    window.setGeometry(300, 300, 1_050, 680)
    moved_away = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() != moved_away
    assert window.geometry().y() == 60
    assert window.geometry().height() == 700


def test_a_first_launch_keeps_the_default_size(window: MainWindow) -> None:
    """Nothing saved yet is not a reason to move the window built moments ago."""
    app_module.QSettings().remove(WINDOW_GEOMETRY_KEY)
    before = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() == before


def test_a_saved_value_that_is_not_a_geometry_is_ignored(window: MainWindow) -> None:
    """Settings can hold anything a past version wrote, or a hand-edited registry key."""
    app_module.QSettings().setValue(WINDOW_GEOMETRY_KEY, "not a geometry")
    before = window.geometry()

    window._restore_window_geometry()

    assert window.geometry() == before


def test_a_window_off_every_screen_is_not_treated_as_reachable(window: MainWindow) -> None:
    """The backstop's own question, asked directly: is any of this window grabbable?"""
    window.setGeometry(6_000, 6_000, 1_100, 700)
    assert window._is_on_an_attached_screen() is False

    window.setGeometry(0, 0, 1_100, 700)
    assert window._is_on_an_attached_screen() is True


def test_a_window_barely_peeking_onto_a_screen_does_not_count(window: MainWindow) -> None:
    """A few pixels of edge is not something a player can find, let alone drag."""
    window.setGeometry(-1_060, 0, 1_100, 700)

    assert window._is_on_an_attached_screen() is False
