from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt

from osrs_toolkit.app import MainWindow
from osrs_toolkit.models import MarketPoint


def _stale_position(window: MainWindow) -> int:
    position_id = window._journal.track(1234, "Antidote++(3)", 70, 7_125, 8_180)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(1, 8_180)], [(70, 7_125)]
    )
    return position_id


def _row_of(window: MainWindow, position_id: int) -> int:
    return next(
        row
        for row in range(window.journal_table.rowCount())
        if window.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == position_id
    )


def _set_live_point(window: MainWindow, item_id: int, *, sell_target: int) -> None:
    window._points = [
        MarketPoint(
            item_id=item_id,
            high=sell_target,
            low=sell_target - 200,
            high_time=1_700_000_000,
            low_time=1_700_000_000,
            volume_5m=1_000,
            volume_1h=10_000,
        )
    ]


def test_the_tooltip_states_the_asking_and_live_prices(window: MainWindow) -> None:
    position_id = _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()

    tooltip = window.journal_table.item(_row_of(window, position_id), 2).toolTip()

    assert "8,180 gp" in tooltip, "the asking price must be spelled out, not just described"
    assert "7,600 gp" in tooltip, "the live suggestion must be spelled out, not just described"


def test_a_position_that_is_not_flagged_gets_no_attention_tooltip(window: MainWindow) -> None:
    position_id = window._journal.track(1234, "Antidote++(3)", 70, 7_125, 7_600)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(1, 7_600)], [(70, 7_125)]
    )
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()

    row = _row_of(window, position_id)
    assert window.journal_table.item(row, 2).toolTip() == "Antidote++(3)"


def test_relisting_at_the_live_suggestion_clears_the_flag(window: MainWindow) -> None:
    position_id = _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()
    assert window.journal_attention.text() == "Needs attention\n1"

    window._journal.apply_offer_opened(
        item_id=1234,
        item_name="Antidote++(3)",
        side="sell",
        total_quantity=69,
        offer_price=7_600,
    )
    window._render_journal()

    assert window.journal_attention.text() == "Needs attention\n0"
    assert window._journal.list_tracked()[0].position_id == position_id


def test_the_flag_survives_a_relist_that_is_still_above_the_market(window: MainWindow) -> None:
    _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._journal.apply_offer_opened(
        item_id=1234,
        item_name="Antidote++(3)",
        side="sell",
        total_quantity=69,
        offer_price=8_000,
    )
    window._render_journal()

    assert window.journal_attention.text() == "Needs attention\n1"
    row = _row_of(window, window._journal.list_tracked()[0].position_id)
    tooltip = window.journal_table.item(row, 2).toolTip()
    assert "8,000 gp" in tooltip
    assert "8,180 gp" not in tooltip


def test_every_cell_on_a_flagged_row_explains_the_flag(window: MainWindow) -> None:
    position_id = _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()
    row = _row_of(window, position_id)

    for column in range(window.journal_table.columnCount()):
        assert "This ask looks stale." in window.journal_table.item(row, column).toolTip(), (
            f"column {column} leaves the flag unexplained"
        )


def test_the_flagged_row_keeps_its_own_profit_explanation(window: MainWindow) -> None:
    position_id = _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()

    tooltip = window.journal_table.item(_row_of(window, position_id), 8).toolTip()
    assert "This ask looks stale." in tooltip
    assert "Realized profit from recorded sale fills." in tooltip


def test_the_tooltip_is_broken_into_short_lines(window: MainWindow) -> None:
    position_id = _stale_position(window)
    _set_live_point(window, 1234, sell_target=7_600)
    window._render_journal()

    tooltip = window.journal_table.item(_row_of(window, position_id), 2).toolTip()
    lines = tooltip.splitlines()
    assert len(lines) == 3
    assert max(len(line) for line in lines) <= 60


def _stale_position_for(window: MainWindow, item_id: int, name: str) -> int:
    position_id = window._journal.track(item_id, name, 70, 7_125, 8_180)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(1, 8_180)], [(70, 7_125)]
    )
    return position_id


def test_clicking_the_card_reveals_the_newest_flagged_row(window: MainWindow) -> None:
    oldest = _stale_position(window)
    newest = _stale_position_for(window, 5678, "Antidote++(4)")
    _set_live_point(window, 1234, sell_target=7_600)
    only_point = window._points[0]
    window._points = [only_point, replace(only_point, item_id=5678)]
    window._render_journal()
    assert window._attention_positions[0] == newest

    window._reveal_attention_positions()

    selected = window.journal_table.selectionModel().selectedRows()
    assert [index.row() for index in selected] == [_row_of(window, newest)]
    assert _row_of(window, newest) != _row_of(window, oldest)
    assert window._journal_flasher.is_lit(oldest)
    assert window._journal_flasher.is_lit(newest)
