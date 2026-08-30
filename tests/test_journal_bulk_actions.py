"""Selecting several journal rows and acting on all of them.

Editing twenty positions one dialog at a time is the thing this replaces, so what these pin
down is that a selection of several reaches a batch editor at all, that the batch editor
cannot touch the per-row numbers, and that one row still gets the full single-row editor it
always had.
"""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QAbstractItemView, QDialog, QMessageBox

from osrs_toolkit.app import MainWindow, RetagTradesDialog
from osrs_toolkit.journal import UNCHANGED


def _position(window: MainWindow, name: str, quantity: int = 100) -> int:
    """A position that has bought its full quantity, so it carries a real average."""
    position_id = window._journal.track(1234, name, quantity, 2_000, 2_500)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(quantity, 1_900)])
    return position_id


def _select_rows(window: MainWindow, rows: list[int]) -> None:
    table = window.journal_table
    table.clearSelection()
    for row in rows:
        table.selectRow(row)
        table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)


# -- the table has to allow it at all --------------------------------------------------------


def test_the_journal_table_allows_a_multi_row_selection(window: MainWindow) -> None:
    """Every table was SingleSelection, so there was no way to ask for a batch."""
    assert window.journal_table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection


def test_other_tables_are_left_single_select(window: MainWindow) -> None:
    """Only a table whose actions can act on a batch should offer a batch selection."""
    assert window.loot_log_table.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection


def test_the_button_says_how_many_it_will_change(window: MainWindow) -> None:
    for name in ("Shark", "Karambwan", "Prayer potion(4)"):
        _position(window, name)
    window._render_journal()

    _select_rows(window, [0, 1, 2])
    window._journal_selection_changed()
    assert window.journal_update_button.text() == "Update 3 selected trades"

    _select_rows(window, [0])
    window._journal_selection_changed()
    assert window.journal_update_button.text() == "Update selected trade"


# -- which editor opens ----------------------------------------------------------------------


def test_one_row_still_gets_the_full_single_row_editor(window: MainWindow) -> None:
    """The batch editor cannot touch fills, so a single row must not be routed to it."""
    _position(window, "Shark")
    window._render_journal()
    _select_rows(window, [0])

    with (
        patch("osrs_toolkit.app.UpdateTrackedTradeDialog") as single,
        patch("osrs_toolkit.app.RetagTradesDialog") as batch,
    ):
        single.return_value.exec.return_value = QDialog.DialogCode.Rejected
        single.STATUSES = ["Bought"]
        window._update_selected_trade()

    assert batch.called is False


def test_several_rows_reach_the_batch_editor(window: MainWindow) -> None:
    for name in ("Shark", "Karambwan"):
        _position(window, name)
    window._render_journal()
    _select_rows(window, [0, 1])

    with patch("osrs_toolkit.app.RetagTradesDialog") as batch:
        batch.return_value.exec.return_value = QDialog.DialogCode.Rejected
        window._update_selected_trade()

    assert batch.called is True
    assert batch.call_args[0][0] == 2  # told how many it is editing


# -- applying it ------------------------------------------------------------------------------


def test_refiling_a_selection_leaves_every_average_alone(window: MainWindow) -> None:
    """The batch write must never go near a fill or an average -- they differ per row, and
    losing them to one wrong click is exactly what a batch editor risks."""
    for name in ("Shark", "Karambwan", "Prayer potion(4)"):
        _position(window, name)
    window._render_journal()
    _select_rows(window, [0, 1, 2])

    with (
        patch("osrs_toolkit.app.RetagTradesDialog") as batch,
        patch("osrs_toolkit.app.QMessageBox.information"),
    ):
        dialog = batch.return_value
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.chosen_status.return_value = "Supplies"
        dialog.chosen_strategy.return_value = None
        dialog.chosen_character.return_value = UNCHANGED
        window._update_selected_trade()

    tracked = window._journal.list_tracked()
    assert {trade.status for trade in tracked} == {"Supplies"}
    assert all(trade.actual_buy == 1_900 for trade in tracked)
    assert all(trade.bought_quantity == 100 for trade in tracked)


def test_deleting_a_selection_removes_all_of_them(window: MainWindow) -> None:
    for name in ("Shark", "Karambwan", "Prayer potion(4)"):
        _position(window, name)
    window._render_journal()
    _select_rows(window, [0, 1, 2])

    with patch("osrs_toolkit.app.QMessageBox.question") as question:
        question.return_value = QMessageBox.StandardButton.Yes
        window._delete_selected_trade()

    assert window._journal.list_tracked() == []


# -- the dialog's own rules -------------------------------------------------------------------


def test_the_dialog_starts_on_leave_unchanged_everywhere(qt_app) -> None:
    dialog = RetagTradesDialog(3, ["Balanced (1–4h)"], [("abc", "Wolklaw")])
    try:
        assert dialog.chosen_status() is None
        assert dialog.chosen_strategy() is None
        assert dialog.changes_anything() is False
    finally:
        dialog.deleteLater()


def test_the_dialog_warns_before_completed_rather_than_after(qt_app) -> None:
    """Finding out from the summary afterwards reads like the app ignored you."""
    dialog = RetagTradesDialog(3, ["Balanced (1–4h)"], [("abc", "Wolklaw")])
    try:
        dialog.status.setCurrentText("Completed")
        assert "Completed" in dialog.warning.text()

        dialog.status.setCurrentText("Supplies")
        assert dialog.warning.text() == ""
    finally:
        dialog.deleteLater()


def test_a_character_choice_is_distinguishable_from_leaving_it_alone(qt_app) -> None:
    dialog = RetagTradesDialog(3, ["Balanced (1–4h)"], [("abc", "Wolklaw"), (None, "Nobody")])
    try:
        assert dialog.chosen_character() is UNCHANGED

        dialog.character.setCurrentText("Wolklaw")
        assert dialog.chosen_character() == "abc"

        dialog.character.setCurrentText("Nobody")
        assert dialog.chosen_character() is None
        assert dialog.changes_anything() is True
    finally:
        dialog.deleteLater()
