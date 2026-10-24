from __future__ import annotations

from osrs_toolkit.app import MainWindow, UpdateTrackedTradeDialog
from osrs_toolkit.models import MarketPoint


def _flip(window: MainWindow, *, realized: bool = True) -> int:
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    if realized:
        window._journal.update_tracked(
            position_id, "Completed", None, None, [(100, 2_500)], [(100, 2_000)]
        )
    return position_id


def _supplies(window: MainWindow) -> int:
    position_id = window._journal.track(379, "Lobster", 5_000, 150, 150)
    window._journal.update_tracked(
        position_id, "Supplies", None, None, [(5_000, 150)], [(5_000, 150)]
    )
    return position_id


def test_supplies_is_a_selectable_status_in_the_update_dialog() -> None:
    assert "Supplies" in UpdateTrackedTradeDialog.STATUSES


def test_supplies_rows_still_show_under_all_statuses(window: MainWindow) -> None:
    _flip(window)
    _supplies(window)
    window._render_journal()

    assert window.journal_status_filter.currentText() == "All statuses"
    assert window.journal_table.rowCount() == 2


def test_supplies_status_filter_shows_only_supplies(window: MainWindow) -> None:
    _flip(window)
    _supplies(window)
    window.journal_status_filter.setCurrentText("Supplies")
    window._render_journal()

    assert window.journal_table.rowCount() == 1
    assert window.journal_table.item(0, 2).text() == "Lobster"


def test_a_specific_status_filter_excludes_supplies(window: MainWindow) -> None:
    _supplies(window)
    window.journal_status_filter.setCurrentText("Bought")
    window._render_journal()

    assert window.journal_table.rowCount() == 0


def test_active_trades_filter_excludes_supplies(window: MainWindow) -> None:
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(position_id, "Supplies", None, None)
    window.journal_status_filter.setCurrentText("Active trades")
    window._render_journal()

    assert window.journal_table.rowCount() == 0


def test_supplies_are_excluded_from_the_summary_cards_under_all_statuses(
    window: MainWindow,
) -> None:
    _flip(window)
    _supplies(window)
    window._render_journal()

    assert window.journal_invested.text().splitlines()[1] == "200,000 gp"


def test_the_cards_stay_flip_only_even_when_the_supplies_filter_is_selected(
    window: MainWindow,
) -> None:
    _flip(window)
    _supplies(window)
    window.journal_status_filter.setCurrentText("Supplies")
    window._render_journal()

    assert window.journal_invested.text().splitlines()[1] == "200,000 gp"


def test_a_stale_supplies_ask_never_raises_needs_attention(window: MainWindow) -> None:
    position_id = window._journal.track(379, "Lobster", 5_000, 150, 150)
    window._journal.update_tracked(position_id, "Supplies", None, None, None, [(5_000, 150)])
    window._points = [
        MarketPoint(
            item_id=379,
            high=100,
            low=90,
            high_time=1_700_000_000,
            low_time=1_700_000_000,
            volume_5m=1_000,
            volume_1h=10_000,
        )
    ]
    window._render_journal()

    assert window.journal_attention.text().splitlines()[1] == "0"


def test_performance_page_excludes_supplies_positions(window: MainWindow) -> None:
    _flip(window)
    _supplies(window)
    window._render_journal()

    assert window.performance_positions.text().splitlines()[1] == "1"


def test_moving_a_position_to_supplies_and_back_updates_the_table(window: MainWindow) -> None:
    position_id = _flip(window, realized=False)
    window._render_journal()
    assert window.journal_status_filter.currentText() == "All statuses"
    assert window.journal_table.rowCount() == 1

    window._journal.update_tracked(position_id, "Supplies", None, None)
    window._render_journal()
    window.journal_status_filter.setCurrentText("Bought")
    window._render_journal()
    assert window.journal_table.rowCount() == 0

    window._journal.update_tracked(position_id, "Bought", None, None, None, [(100, 2_000)])
    window._render_journal()
    assert window.journal_table.rowCount() == 1


def test_supplies_spend_tab_reports_total_and_per_item_rows(window: MainWindow) -> None:
    _flip(window)
    _supplies(window)
    window._render_journal()

    assert window.supplies_spend_total.text().splitlines()[1] == "750,000 gp"
    assert window.supplies_spend_table.rowCount() == 1
    assert window.supplies_spend_table.item(0, 0).text() == "Lobster"
    assert window.supplies_spend_empty.isHidden() is True


def test_supplies_spend_tab_shows_empty_state_with_no_supplies(window: MainWindow) -> None:
    _flip(window)
    window._render_journal()

    assert window.supplies_spend_table.rowCount() == 0
    assert window.supplies_spend_empty.isHidden() is False
