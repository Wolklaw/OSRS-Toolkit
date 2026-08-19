"""Regression tests for table columns fitting the window they are shown in.

Column widths start from ``resizeColumnsToContents()``, which measures the single longest
row. A free-text column ("Notes" on PvM Readiness, "Assumption" on Skilling Profit) can
demand more width that way than the whole window has, which used to push every column
after it — including the Guide link column — off the right edge with nothing on screen to
suggest they existed.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import DEFAULT_MAXIMUM_COLUMN_WIDTH, MainWindow, ResponsiveTableWidget
from osrs_toolkit.calculators import SKILL_METHODS
from osrs_toolkit.models import ItemMapping, MarketPoint


def _price_every_skilling_input(window: MainWindow) -> None:
    now = int(time.time())
    item_ids = {
        item.item_id for method in SKILL_METHODS for item in (*method.inputs, *method.outputs)
    }
    window._mappings = {
        item_id: ItemMapping(item_id, f"Item {item_id}", False, 1_000, 100)
        for item_id in item_ids
    }
    window._points = [
        MarketPoint(
            item_id=item_id, high=200, low=150, high_time=now - 60, low_time=now - 60,
            volume_5m=500, volume_1h=5_000,
        )
        for item_id in item_ids
    ]


def _lay_out(
    qt_app: QApplication,
    window: MainWindow,
    table: ResponsiveTableWidget,
    page: str,
    viewport_width: int,
) -> None:
    """Give ``table`` roughly ``viewport_width`` pixels of room, as a real window would.

    The window has to be shown and sized rather than the table resized directly: Qt holds
    back resize events for hidden widgets, so a table nobody has shown never re-lays out
    its columns at all. The chrome around it (sidebar, margins, frame) is measured rather
    than assumed, since it moves with the font the host happens to have.
    """
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index(page))
    window.show()
    qt_app.processEvents()
    chrome = window.width() - table.viewport().width()
    window.resize(viewport_width + chrome, 800)
    qt_app.processEvents()


def _right_edge(table: ResponsiveTableWidget) -> int:
    last = table.columnCount() - 1
    return table.columnViewportPosition(last) + table.columnWidth(last)


def test_pvm_guide_column_stays_on_screen_when_the_window_cannot_fit_every_column(
    qt_app: QApplication, window: MainWindow
) -> None:
    """Regression: the Guide column sat past the right edge on a 1080p monitor."""
    window._render_pvm()
    table = window.pvm_table
    # Comfortably above the point where the columns can no longer be squeezed at all, but
    # well below the width they would take if each got the room its content asked for.
    assert sum(table._floor_widths) + 150 < sum(table._preferred_widths)
    _lay_out(qt_app, window, table, "PvM Readiness", sum(table._floor_widths) + 150)

    assert _right_edge(table) <= table.viewport().width()
    assert all(table.columnWidth(column) > 0 for column in range(table.columnCount()))


def test_skilling_columns_stay_on_screen_when_the_window_cannot_fit_every_column(
    qt_app: QApplication, window: MainWindow
) -> None:
    _price_every_skilling_input(window)
    window.skill_profitable.setChecked(False)
    window._render_skilling()
    table = window.skill_table
    _lay_out(qt_app, window, table, "Skilling Profit", sum(table._floor_widths) + 150)

    assert _right_edge(table) <= table.viewport().width()


def test_columns_still_fill_a_viewport_wider_than_they_asked_for(
    qt_app: QApplication, window: MainWindow
) -> None:
    """Growing to fill the window must survive the shrinking path being added."""
    window._render_pvm()
    table = window.pvm_table
    _lay_out(qt_app, window, table, "PvM Readiness", sum(table._preferred_widths) + 400)

    viewport = table.viewport().width()
    assert viewport - 4 <= _right_edge(table) <= viewport


def test_a_long_notes_column_cannot_outgrow_its_cap(window: MainWindow) -> None:
    """The PvM notes run to full sentences; unbounded, one row sets the column width."""
    window._render_pvm()
    table = window.pvm_table
    longest = max(len(activity_notes) for activity_notes in _pvm_notes(window))

    assert longest > 80, "expected sentence-length notes to size this column against"
    assert table._preferred_widths[5] <= 380
    assert table._preferred_widths[2] <= DEFAULT_MAXIMUM_COLUMN_WIDTH
    assert table._preferred_widths[3] <= DEFAULT_MAXIMUM_COLUMN_WIDTH


def _pvm_notes(window: MainWindow) -> list[str]:
    table = window.pvm_table
    return [table.item(row, 5).text() for row in range(table.rowCount())]


def test_prose_columns_read_left_to_right(window: MainWindow) -> None:
    """A right-aligned sentence elides its beginning and reads as a ragged left edge."""
    window._render_pvm()
    table = window.pvm_table
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    for column in (1, 2, 3, 5, 6):
        assert table.item(0, column).textAlignment() == left, f"column {column}"
    # The figures either side of them keep their right alignment.
    assert table.item(0, 4).textAlignment() == right
