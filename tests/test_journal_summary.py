from __future__ import annotations

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal_presentation import PERIOD_FILTERS


def _partially_sold_position(window: MainWindow) -> int:
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(50, 2_500)], [(100, 2_000)]
    )
    return position_id


def _show_period(window: MainWindow, period: str) -> None:
    window.journal_period_filter.setCurrentText(period)
    window._render_journal()


def _realized_profit_card(window: MainWindow) -> str:
    return window.journal_profit.text().splitlines()[1]


def test_partially_sold_profit_counts_under_every_period(window: MainWindow) -> None:
    _partially_sold_position(window)

    for period in PERIOD_FILTERS:
        _show_period(window, period)
        assert _realized_profit_card(window) == "+22,500 gp", (
            f"realized profit disappeared from the summary under {period!r}"
        )


def test_visible_partial_row_and_summary_card_tell_the_same_story(window: MainWindow) -> None:
    _partially_sold_position(window)
    _show_period(window, "This year")

    table = window.journal_table
    assert table.rowCount() == 1
    assert table.item(0, 1).text() == "● Partially sold"
    assert table.item(0, 8).text() == "+22,500 gp • 50 left"
    assert _realized_profit_card(window) == "+22,500 gp"


def test_capital_traded_counts_only_what_was_actually_bought(window: MainWindow) -> None:
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Partially sold", None, None, [(40, 2_500)], [(40, 2_000)]
    )
    _show_period(window, "All time")

    assert window.journal_invested.text().splitlines()[1] == "80,000 gp"


def test_completed_position_outside_the_period_is_excluded(window: MainWindow) -> None:
    position_id = window._journal.track(1234, "Dragon bones", 10, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(10, 2_500)], [(10, 2_000)]
    )
    _show_period(window, "Today")
    assert _realized_profit_card(window) == "+4,500 gp"

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
    _partially_sold_position(window)
    _show_period(window, "All time")

    assert _card(window.journal_profit) == "+22,500 gp"
    assert _card(window.journal_invested) == "100,000 gp"


def test_both_pages_report_the_same_realized_figures(window: MainWindow) -> None:
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
