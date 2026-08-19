"""Sorting the journal by Status must order rows by the trade lifecycle.

``_JOURNAL_STATUS_ORDER`` alone proves nothing here: the ranks were always right, while
the table sorted by the text of the Status cell instead, because the ranks were attached
to cells only after ``_fill_table`` had already sorted (and re-sorted) the rows. So this
drives the real table and reads back what a user would see.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from osrs_toolkit.app import MainWindow
from osrs_toolkit.models import MarketPoint

# What each status needs to be a valid position: sale fills against a 100-unit buy.
_SALE_FILLS: dict[str, list[tuple[int, int]]] = {
    "Bought": [],
    "Listed for sale": [],
    "Partially sold": [(40, 2_500)],
    "Completed": [(100, 2_500)],
    "Cancelled": [],
}


def _position(window: MainWindow, name: str, status: str) -> int:
    position_id = window._journal.track(1234, name, 100, 2_000, 2_500)
    if status != "Pending buy":
        window._journal.update_tracked(
            position_id, status, None, None, _SALE_FILLS[status], [(100, 2_000)]
        )
    return position_id


def _visible_statuses(window: MainWindow) -> list[str]:
    table = window.journal_table
    return [table.item(row, 1).text().removeprefix("● ") for row in range(table.rowCount())]


def _sort_by_status(window: MainWindow, order: Qt.SortOrder) -> None:
    """Sort by Status the way clicking the header does, then re-render as the app would."""
    window.journal_table.sortItems(1, order)
    window._render_journal()


def test_status_column_sorts_by_lifecycle_stage(window: MainWindow) -> None:
    """Regression: Cancelled used to land between Bought and Partially sold, alphabetically."""
    _position(window, "Dragon bones", "Cancelled")
    _position(window, "Yew logs", "Partially sold")
    _position(window, "Rune scimitar", "Bought")
    _position(window, "Magic logs", "Completed")
    _position(window, "Adamant brutal", "Pending buy")
    _position(window, "Tarromin", "Listed for sale")

    _sort_by_status(window, Qt.SortOrder.AscendingOrder)

    assert _visible_statuses(window) == [
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Cancelled",
    ]


def test_status_column_reverses_cleanly(window: MainWindow) -> None:
    _position(window, "Dragon bones", "Cancelled")
    _position(window, "Yew logs", "Partially sold")
    _position(window, "Adamant brutal", "Pending buy")

    _sort_by_status(window, Qt.SortOrder.DescendingOrder)

    assert _visible_statuses(window) == ["Cancelled", "Partially sold", "Pending buy"]


def test_every_row_keeps_its_own_status_colour_and_stored_status(window: MainWindow) -> None:
    """Decorating after a sort walked a moving list, so some rows were skipped entirely."""
    _position(window, "Dragon bones", "Cancelled")
    _position(window, "Yew logs", "Partially sold")
    _position(window, "Rune scimitar", "Bought")
    _position(window, "Magic logs", "Completed")

    _sort_by_status(window, Qt.SortOrder.AscendingOrder)

    table = window.journal_table
    for row in range(table.rowCount()):
        cell = table.item(row, 1)
        display_status = cell.text().removeprefix("● ")
        assert cell.data(Qt.ItemDataRole.UserRole) == display_status
        assert cell.foreground().color().name() == window._journal_status_colors[display_status]
        assert cell.font().bold(), f"{display_status} row was never decorated"


def test_the_attention_colour_lands_on_the_flagged_row_and_no_other(window: MainWindow) -> None:
    """A row moving mid-decoration painted the next row's Item cell with its own warning.

    The ⚠ is baked into the cell text as the rows are built; the colour was applied
    afterwards by row index. Once a row could move between the two, an unflagged item
    could come out amber with no ⚠ beside it, and a flagged one plain.
    """
    stale = window._journal.track(1234, "Antidote++(3)", 70, 7_125, 8_180)
    window._journal.update_tracked(stale, "Partially sold", None, None, [(1, 8_180)], [(70, 7_125)])
    # Asking 8,180 against a live suggestion of 7,600 — a stale ask, so this row is flagged.
    window._points = [
        MarketPoint(
            item_id=1234,
            high=7_600,
            low=7_400,
            high_time=1_700_000_000,
            low_time=1_700_000_000,
            volume_5m=1_000,
            volume_1h=10_000,
        )
    ]
    # No market point of their own, so nothing to flag these against.
    window._journal.track(555, "Adamant brutal", 100, 300, 340)
    window._journal.track(556, "Tarromin", 100, 280, 300)
    _position(window, "Dragon bones", "Cancelled")
    _position(window, "Magic logs", "Completed")

    _sort_by_status(window, Qt.SortOrder.AscendingOrder)

    table = window.journal_table
    flagged = 0
    for row in range(table.rowCount()):
        cell = table.item(row, 2)
        is_amber = cell.foreground().color().name() == window._warning_color
        assert is_amber == cell.text().startswith("⚠ "), (
            f"{cell.text()!r} was coloured as though flagged: {is_amber}"
        )
        flagged += is_amber
    assert flagged == 1, "the one stale ask must still be flagged"
