from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import MainWindow


def _shortcuts(window: MainWindow) -> set[str]:
    return {
        shortcut.key().toString()
        for shortcut in window.findChildren(QShortcut)
        if not shortcut.key().isEmpty()
    }


def test_every_page_has_a_number_of_its_own(window: MainWindow) -> None:
    assert {
        MainWindow.page_shortcut(index) for index in range(len(MainWindow.NAV_ITEMS))
    } <= _shortcuts(window)


def test_the_market_refresh_answers_to_the_usual_key(window: MainWindow) -> None:
    assert QKeySequence(QKeySequence.StandardKey.Refresh).toString() in _shortcuts(window)


def test_the_shortcuts_are_advertised_where_the_pages_are(window: MainWindow) -> None:
    for index, title in enumerate(MainWindow.NAV_ITEMS):
        assert window.nav.item(index).toolTip() == f"{title}  ({MainWindow.page_shortcut(index)})"


def test_pressing_the_number_changes_the_page(window: MainWindow, qt_app: QApplication) -> None:
    window.show()
    window.activateWindow()
    qt_app.processEvents()
    journal = MainWindow.NAV_ITEMS.index("Trade Journal")

    QTest.keyClick(window, Qt.Key.Key_1 + journal, Qt.KeyboardModifier.ControlModifier)
    qt_app.processEvents()

    assert window.pages.currentIndex() == journal
    assert window.nav.currentRow() == journal


def test_no_two_pages_share_a_shortcut() -> None:
    keys = [MainWindow.page_shortcut(index) for index in range(len(MainWindow.NAV_ITEMS))]

    assert len(set(keys)) == len(keys)
    assert all(QKeySequence(key).count() == 1 for key in keys)
