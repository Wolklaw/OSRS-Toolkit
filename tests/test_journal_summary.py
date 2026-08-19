"""The journal summary cards must agree with the rows they summarize.

These drive the real ``MainWindow._render_journal`` rather than the pure period helpers,
because the original defect was two call sites applying *different* scoping rules — which
only shows up when both run against the same data.
"""

from __future__ import annotations

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal_presentation import PERIOD_FILTERS


def _partially_sold_position(window: MainWindow) -> int:
    """Buy 100 @ 2,000, sell 50 of them @ 2,500 — 22,500 gp realized, 50 still held."""
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(50, 2_500)], [(100, 2_000)]
    )
    return position_id


def _show_period(window: MainWindow, period: str) -> None:
    """Select a period and re-render.

    Setting a combo box to the value it already holds emits no signal, so the render has
    to be explicit — otherwise the assertions read cards built before the test's data.
    """
    window.journal_period_filter.setCurrentText(period)
    window._render_journal()


def _realized_profit_card(window: MainWindow) -> str:
    return window.journal_profit.text().splitlines()[1]


def test_partially_sold_profit_counts_under_every_period(window: MainWindow) -> None:
    """Regression: 22,500 gp of realized profit used to appear only under 'All time'."""
    _partially_sold_position(window)

    for period in PERIOD_FILTERS:
        _show_period(window, period)
        assert _realized_profit_card(window) == "+22,500 gp", (
            f"realized profit disappeared from the summary under {period!r}"
        )


def test_visible_partial_row_and_summary_card_tell_the_same_story(window: MainWindow) -> None:
    """The row shows +22,500 gp; the card above it must not read zero."""
    _partially_sold_position(window)
    _show_period(window, "This year")

    table = window.journal_table
    assert table.rowCount() == 1
    assert table.item(0, 1).text() == "● Partially sold"
    assert table.item(0, 8).text() == "+22,500 gp • 50 left"
    assert _realized_profit_card(window) == "+22,500 gp"


def test_capital_traded_counts_only_what_was_actually_bought(window: MainWindow) -> None:
    """A half-filled buy has spent half the money, not the full planned outlay."""
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    # Only 40 of the 100 filled, then sold on.
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(40, 2_500)], [(40, 2_000)]
    )
    _show_period(window, "All time")

    assert window.journal_invested.text().splitlines()[1] == "80,000 gp"


def test_completed_position_outside_the_period_is_excluded(window: MainWindow) -> None:
    """The period filter still scopes finished history — that part was never broken."""
    position_id = window._journal.track(1234, "Dragon bones", 10, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(10, 2_500)], [(10, 2_000)]
    )
    _show_period(window, "Today")
    assert _realized_profit_card(window) == "+4,500 gp"

    # Backdate the completion; it should drop out of "Today" but stay in "All time".
    with window._journal._connect() as connection:
        connection.execute(
            "UPDATE tracked_trades SET completed_at = ? WHERE position_id = ?",
            ("2020-01-01T00:00:00+00:00", position_id),
        )
    window._render_journal()
    assert _realized_profit_card(window) == "0 gp"

    _show_period(window, "All time")
    assert _realized_profit_card(window) == "+4,500 gp"


def _card(label) -> str:  # type: ignore[no-untyped-def]
    return label.text().splitlines()[1]


def test_capital_traded_counts_only_the_part_that_sold(window: MainWindow) -> None:
    """Bought 100 @ 2,000 but only sold 50 of them: half the outlay is still in the trade,
    not capital the trade has produced a result with. Pairing the full 200,000 gp with the
    50-unit profit beside it charged twice the capital against half the goods."""
    _partially_sold_position(window)
    _show_period(window, "All time")

    assert _card(window.journal_profit) == "+22,500 gp"
    assert _card(window.journal_invested) == "100,000 gp"


def test_both_pages_report_the_same_realized_figures(window: MainWindow) -> None:
    """Realized profit, win rate, and capital traded appear on the Trade Journal and on
    Performance under the same names. They are one set of figures, so they must be one
    calculation — "Capital traded" once meant the whole outlay here and the cost of what
    sold there, and a part-sold position showed the two cards disagreeing."""
    _partially_sold_position(window)
    window._journal.add("Shark", 1_000, 800, 1_000)
    window.performance_period_filter.setCurrentText("All time")
    _show_period(window, "All time")

    assert _card(window.journal_profit) == _card(window.performance_profit)
    assert _card(window.journal_win_rate) == _card(window.performance_win_rate)
    strategies = window.performance_strategy_table
    capital = sum(
        int(strategies.item(row, 5).text().replace(",", "").removesuffix(" gp"))
        for row in range(strategies.rowCount())
    )
    assert _card(window.journal_invested) == f"{capital:,} gp"
