"""The Performance page grades plans against outcomes, so its arithmetic has to be exact.

The pure aggregations are tested directly against hand-built positions. The last group
drives the real ``MainWindow`` instead, because the one thing a user will notice instantly
is the Performance page and the Trade Journal disagreeing about the same trades.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal import BuyFill, SaleFill, TrackedTrade, TradeRecord
from osrs_toolkit.performance import (
    MANUAL_STRATEGY,
    by_item,
    by_strategy,
    calibration,
    realized_results,
    summarize,
)

NOW = datetime(2026, 8, 15, 18, 0, tzinfo=UTC)


def _position(
    position_id: int = 1,
    *,
    item_name: str = "Dragon bones",
    quantity: int = 10,
    target_buy: int = 1_000,
    target_sell: int = 1_200,
    buys: tuple[tuple[int, int], ...] = ((10, 1_000),),
    sales: tuple[tuple[int, int], ...] = ((10, 1_200),),
    strategy: str = "Balanced (1–4h)",
    status: str = "Completed",
    created_at: datetime = NOW - timedelta(hours=3),
    completed_at: datetime | None = NOW,
) -> TrackedTrade:
    """A tracked position with the weighted-average buy price the repository would store."""
    bought = sum(fill_quantity for fill_quantity, _price in buys)
    actual_buy = (
        round(sum(q * p for q, p in buys) / bought) if bought else None
    )
    return TrackedTrade(
        position_id=position_id,
        created_at=created_at.isoformat(timespec="seconds"),
        item_id=1234,
        item_name=item_name,
        quantity=quantity,
        target_buy=target_buy,
        target_sell=target_sell,
        actual_buy=actual_buy,
        actual_sell=None,
        status=status,
        sale_fills=tuple(
            SaleFill(index, position_id, q, p) for index, (q, p) in enumerate(sales, 1)
        ),
        buy_fills=tuple(
            BuyFill(index, position_id, q, p) for index, (q, p) in enumerate(buys, 1)
        ),
        strategy=strategy,
        completed_at=completed_at.isoformat(timespec="seconds") if completed_at else None,
    )


def _results(*positions: TrackedTrade, period: str = "All time"):
    return realized_results(list(positions), [], period, NOW)


# --- what counts as a result -------------------------------------------------------


def test_a_position_with_no_sale_fills_is_not_a_result() -> None:
    """Bought and waiting is not an outcome; only recorded proceeds are."""
    assert _results(_position(sales=(), status="Bought", completed_at=None)) == []


def test_a_partial_sale_counts_only_the_part_that_sold() -> None:
    """Sell 4 of 10 at 1,200 bought at 1,000: 4 x (1200 - 1000 - 24 tax) = 704 gp on 4,000."""
    (result,) = _results(_position(sales=((4, 1_200),), status="Partially sold", completed_at=None))

    assert result.profit == 704
    assert result.basis == 4_000, "capital at work is the cost of what sold, not the whole buy"


def test_the_period_filter_scopes_finished_history_only() -> None:
    """Same rule as the Journal: a position still in progress never drops out of view."""
    old = _position(1, completed_at=NOW - timedelta(days=90))
    live = _position(2, sales=((5, 1_200),), status="Partially sold", completed_at=None)

    labels = {result.basis for result in _results(old, live, period="Today")}
    assert labels == {5_000}, "the 90-day-old completed position should have dropped out"


def test_manual_trades_count_under_their_own_strategy() -> None:
    """They have no plan to grade, but excluding them would contradict the Journal.

    Buy 10 @ 300, sell @ 400 with 8 gp tax each: 10 x 92 = 920 gp on 3,000 invested.
    """
    record = TradeRecord(
        trade_id=1,
        recorded_at=(NOW - timedelta(hours=1)).isoformat(timespec="seconds"),
        item_name="Yew logs",
        quantity=10,
        buy_price=300,
        sell_price=400,
    )

    (result,) = realized_results([], [record], "All time", NOW)

    assert (result.strategy, result.profit, result.basis) == (MANUAL_STRATEGY, 920, 3_000)
    assert result.hold_hours is None, "a manual entry records the outcome, not the wait"
    assert calibration([], "All time", NOW) == [], "there is no plan to grade"


# --- aggregation -------------------------------------------------------------------


def test_return_on_capital_is_weighted_not_averaged() -> None:
    """A tiny high-ROI flip must not drag the headline number up.

    Small: buy 1 @ 100, sell 1 @ 200 -> 96 gp on 100 (96%).
    Large: buy 1,000 @ 1,000, sell @ 1,010 -> 1,000 x (10 - 20 tax) = -10,000 gp on 1,000,000.
    Weighted return is (96 - 10,000) / 1,000,100, nowhere near the 48% naive mean.
    """
    small = _position(1, quantity=1, target_buy=100, buys=((1, 100),), sales=((1, 200),))
    large = _position(
        2, quantity=1_000, target_buy=1_000, buys=((1_000, 1_000),), sales=((1_000, 1_010),)
    )

    summary = summarize(_results(small, large))

    assert summary.realized_profit == -9_904
    assert summary.capital_traded == 1_000_100
    assert summary.return_on_capital is not None
    assert round(summary.return_on_capital, 4) == round(-9_904 / 1_000_100 * 100, 4)
    assert summary.win_rate == 50.0


def test_median_hold_ignores_positions_with_no_known_duration() -> None:
    """Backfilled history has completed_at == created_at; that is unknown, not instant."""
    real = _position(1, created_at=NOW - timedelta(hours=5), completed_at=NOW)
    backfilled = _position(2, created_at=NOW, completed_at=NOW)

    summary = summarize(_results(real, backfilled))

    assert summary.positions == 2
    assert summary.median_hold_hours == 5.0


def test_strategies_are_ranked_by_realized_profit() -> None:
    quick = _position(1, strategy="Quick", sales=((10, 1_100),))
    overnight = _position(2, strategy="Overnight", sales=((10, 1_500),))

    rows = by_strategy(_results(quick, overnight))

    assert [row.label for row in rows] == ["Overnight", "Quick"]
    assert rows[0].realized_profit > rows[1].realized_profit
    assert rows[0].win_rate == 100.0


def test_items_traded_once_are_hidden_until_asked_for() -> None:
    repeated = [_position(index, item_name="Yew logs") for index in (1, 2)]
    one_off = _position(3, item_name="Rune platebody")

    assert [row.label for row in by_item(_results(*repeated, one_off))] == ["Yew logs"]
    assert len(by_item(_results(*repeated, one_off), minimum_positions=1)) == 2


# --- plan vs. actual ---------------------------------------------------------------


def test_buy_drift_is_favourable_when_the_fill_beat_the_target() -> None:
    """Planned 1,000, paid 950: 5% under, and under is good on the buy side."""
    position = _position(buys=((10, 950),), sales=())

    (buy_row,) = calibration([position], "All time", NOW)

    assert buy_row.label == "Buy price"
    assert (buy_row.planned, buy_row.actual) == (1_000, 950)
    assert buy_row.drift == -5.0
    assert buy_row.tone == "positive", "paying under the target is a win, not a red cell"


def test_sell_drift_is_unfavourable_when_the_fill_missed_the_target() -> None:
    position = _position(sales=((10, 1_080),))

    rows = {row.label: row for row in calibration([position], "All time", NOW)}

    assert rows["Sell price"].actual == 1_080
    assert rows["Sell price"].drift == -10.0
    assert rows["Sell price"].tone == "negative"


def test_price_drift_is_weighted_by_quantity() -> None:
    """One 1,000-unit position must outweigh a single-unit one, not tie with it."""
    big = _position(1, quantity=1_000, buys=((1_000, 1_100),), sales=())
    small = _position(2, quantity=1, buys=((1, 500),), sales=())

    (buy_row,) = calibration([big, small], "All time", NOW)

    assert buy_row.positions == 2
    # (1,000 x 1,100 + 1 x 500) / 1,001 = 1,099.4, not the 800 an unweighted mean gives.
    assert buy_row.actual == 1_099


def test_planned_profit_is_compared_over_the_quantity_that_actually_sold() -> None:
    """Half sold: the plan's promise is halved too, so both sides cover the same goods.

    Plan is 1,000 -> 1,200 (24 gp tax), so 176 gp per unit; 5 sold promises 880 gp.
    Reality sold 5 @ 1,150 (23 gp tax) for 5 x 127 = 635 gp.
    """
    position = _position(sales=((5, 1_150),), status="Partially sold", completed_at=None)

    rows = {row.label: row for row in calibration([position], "All time", NOW)}
    profit_row = rows["Profit after tax"]

    assert (profit_row.planned, profit_row.actual) == (880, 635)
    assert profit_row.tone == "negative"
    assert profit_row.signed is True


def test_a_bought_position_with_no_fill_rows_still_grades_its_buy() -> None:
    """History predating per-fill prices only gains fill rows at the next start-up.

    Until then it has an ``actual_buy`` and no ``buy_fills``, and dropping it would leave
    the buy row covering fewer positions than the sell row beside it for no visible reason.
    """
    position = replace(_position(buys=(), sales=((10, 1_200),)), actual_buy=980)

    rows = {row.label: row for row in calibration([position], "All time", NOW)}

    assert rows["Buy price"].actual == 980
    assert rows["Buy price"].positions == rows["Sell price"].positions == 1


def test_a_position_with_no_fills_is_not_graded() -> None:
    """A Pending buy has a plan but no reality to compare it against."""
    pending = _position(buys=(), sales=(), status="Pending buy", completed_at=None)
    assert calibration([pending], "All time", NOW) == []


# --- the page itself ---------------------------------------------------------------


def _card(label) -> str:
    return label.text().splitlines()[1]


def test_performance_and_journal_report_the_same_realized_profit(window: MainWindow) -> None:
    """The two pages read the same database; disagreeing cards would read as a bug."""
    tracked = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        tracked, "Partially sold", None, None, [(50, 2_500)], [(100, 2_000)]
    )
    window._journal.add("Yew logs", 10, 300, 400)
    window._render_journal()

    assert _card(window.journal_profit) == _card(window.performance_profit)
    assert _card(window.performance_profit) != "0 gp"


def test_manual_entries_appear_as_their_own_strategy_row(window: MainWindow) -> None:
    window._journal.add("Yew logs", 10, 300, 400)
    window._render_journal()

    table = window.performance_strategy_table
    labels = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert labels == [MANUAL_STRATEGY]
    # isHidden, not isVisible: the window itself is never shown offscreen, so isVisible is
    # False for every widget and would assert nothing.
    assert window.performance_strategy_empty.isHidden(), "the empty state should give way"


def test_an_empty_journal_shows_empty_states_and_no_rows(window: MainWindow) -> None:
    window._render_journal()

    assert _card(window.performance_profit) == "0 gp"
    assert _card(window.performance_return) == "—"
    assert _card(window.performance_hold) == "—"
    assert window.performance_strategy_table.rowCount() == 0
    assert window.performance_plan_table.rowCount() == 0
    assert window.performance_strategy_empty.isHidden() is False


def test_the_item_checkbox_reveals_one_off_trades(window: MainWindow) -> None:
    """Off by default so the table ranks items actually traded more than once."""
    for _ in range(2):
        window._journal.add("Yew logs", 10, 300, 400)
    window._journal.add("Rune platebody", 1, 38_000, 39_600)
    window._render_journal()

    table = window.performance_item_table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["Yew logs"]

    window.performance_item_all.setChecked(True)

    assert sorted(table.item(row, 0).text() for row in range(table.rowCount())) == [
        "Rune platebody",
        "Yew logs",
    ]


def test_the_page_refreshes_when_a_trade_is_recorded(window: MainWindow) -> None:
    """_render_journal is the single hook; a mutation must reach this page through it."""
    position_id = window._journal.track(1234, "Dragon bones", 10, 2_000, 2_500)
    window._render_journal()
    assert window.performance_strategy_table.rowCount() == 0, "a plan alone is not a result"

    window._journal.update_tracked(
        position_id, "Completed", None, None, [(10, 2_500)], [(10, 2_000)]
    )
    window._render_journal()

    assert window.performance_strategy_table.rowCount() == 1
    assert _card(window.performance_profit) == "+4,500 gp"
