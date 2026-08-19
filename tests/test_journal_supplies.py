"""Supplies (quest/skilling buys that aren't flips) must not pollute flip totals.

'Supplies' is set the same way as any other status — the Update dialog's Status dropdown,
which drives ``JournalRepository.update_tracked`` — not a separate mechanism. Once a
position is Supplies it has to disappear from the summary cards, the needs-attention flag,
and the Performance page, everywhere the app claims to be reporting trading results,
without disturbing positions still bought and sold as flips.
"""

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
    """Buying lobsters for a quest, then marking the position Supplies via the same
    update_tracked call the Update dialog makes — 'Completed' with a same-price sale fill
    purely so it has a realized result to test the summary cards against."""
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
    """The 750,000 gp spent on lobsters must not show up as capital traded on a page whose
    whole point is grading flip profit — regardless of which status filter is selected."""
    _flip(window)
    _supplies(window)
    window._render_journal()

    assert window.journal_invested.text().splitlines()[1] == "200,000 gp"


def test_the_cards_stay_flip_only_even_when_the_supplies_filter_is_selected(
    window: MainWindow,
) -> None:
    """The summary cards were never scoped by the status filter (only by period) — picking
    'Supplies' changes which rows are listed below, not what the cards above report."""
    _flip(window)
    _supplies(window)
    window.journal_status_filter.setCurrentText("Supplies")
    window._render_journal()

    assert window.journal_invested.text().splitlines()[1] == "200,000 gp"


def test_a_stale_supplies_ask_never_raises_needs_attention(window: MainWindow) -> None:
    """A supplies buy sitting well below the live sell suggestion is not a stale flip ask —
    it was never listed for sale on purpose, so it must never trip the flag."""
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
    # isHidden() reflects the widget's own explicit hide/show state; isVisible() would also
    # depend on the tab being the active one and the window actually being shown on screen,
    # neither of which this offscreen test does.
    assert window.supplies_spend_empty.isHidden() is True


def test_supplies_spend_tab_shows_empty_state_with_no_supplies(window: MainWindow) -> None:
    _flip(window)
    window._render_journal()

    assert window.supplies_spend_table.rowCount() == 0
    assert window.supplies_spend_empty.isHidden() is False
