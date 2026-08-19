from datetime import UTC, datetime, timedelta, timezone

import pytest

from osrs_toolkit.journal_presentation import (
    JOURNAL_STATUS_FILTERS,
    PERIOD_FILTERS,
    JournalPLPresentation,
    journal_display_status,
    journal_pl_presentation,
    journal_status_matches,
    tracked_position_within_period,
    trade_needs_attention,
    trade_within_period,
)


def test_status_filter_labels_are_stable() -> None:
    assert JOURNAL_STATUS_FILTERS == (
        "All statuses",
        "Active trades",
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Cancelled",
        "Supplies",
    )


def test_supplies_filter_matches_only_supplies() -> None:
    assert journal_status_matches("Supplies", "Supplies") is True
    assert journal_status_matches("Bought", "Supplies") is False


def test_supplies_is_not_an_active_trade() -> None:
    assert journal_status_matches("Supplies", "Active trades") is False


def test_all_statuses_includes_supplies() -> None:
    assert journal_status_matches("Supplies", "All statuses") is True


@pytest.mark.parametrize(
    "status",
    ["Pending buy", "Bought", "Listed for sale", "Partially sold"],
)
def test_active_filter_includes_every_open_trade_status(status: str) -> None:
    assert journal_status_matches(status, "Active trades") is True


@pytest.mark.parametrize(
    "selected_filter",
    ["Pending buy", "Bought", "Listed for sale", "Partially sold"],
)
def test_individual_active_filter_matches_only_its_status(selected_filter: str) -> None:
    other_status = "Bought" if selected_filter != "Bought" else "Pending buy"

    assert journal_status_matches(selected_filter, selected_filter) is True
    assert journal_status_matches(other_status, selected_filter) is False


@pytest.mark.parametrize("status", ["Completed", "Completed (manual)"])
def test_completed_filter_includes_tracked_and_manual_entries(status: str) -> None:
    assert journal_status_matches(status, "Completed") is True
    assert journal_status_matches(status, "Active trades") is False


def test_cancelled_filter_is_distinct_from_active_and_completed() -> None:
    assert journal_status_matches("Cancelled", "Cancelled") is True
    assert journal_status_matches("Cancelled", "Active trades") is False
    assert journal_status_matches("Cancelled", "Completed") is False
    assert journal_status_matches("Cancelled", "All statuses") is True


def test_unknown_status_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown journal status filter"):
        journal_status_matches("Bought", "Open-ish")


@pytest.mark.parametrize(
    ("estimated_profit", "tone"),
    [(12_345, "muted"), (-12_345, "negative"), (0, "neutral")],
)
def test_open_profit_is_clearly_an_estimate_with_risk_aware_tone(
    estimated_profit: int,
    tone: str,
) -> None:
    sign = "+" if estimated_profit > 0 else ""
    assert journal_pl_presentation("Bought", estimated_profit, None) == JournalPLPresentation(
        f"Est. {sign}{estimated_profit:,} gp",
        tone,
    )


def test_cancelled_trade_without_sale_fills_has_neutral_profit() -> None:
    presentation = journal_pl_presentation("Cancelled", 12_345, None)
    assert presentation == JournalPLPresentation(
        "—",
        "neutral",
    )
    assert presentation.tooltip == (
        "Cancelled without realized sale proceeds; excluded from win rate."
    )


def test_a_supplies_position_with_nothing_realized_shows_no_projection() -> None:
    """Regression: a Supplies position's target_sell mirrors target_buy, so the generic
    estimate branch projected roughly "the GE tax on reselling at cost" — a large, alarming,
    and meaningless negative number for something never meant to be sold."""
    presentation = journal_pl_presentation("Supplies", -257_600, None)

    assert presentation.text == "—"
    assert presentation.tone == "neutral"
    assert presentation.tooltip != (
        "Cancelled without realized sale proceeds; excluded from win rate."
    )
    assert "not tracked for profit" in presentation.tooltip


def test_a_supplies_position_that_actually_sold_something_still_shows_it() -> None:
    """If a Supplies item did get partly sold off, that's a real result worth showing —
    only the *projection* for what hasn't sold gets suppressed."""
    presentation = journal_pl_presentation("Supplies", -257_600, 4_500, remaining_quantity=10)

    assert presentation.text == "+4,500 gp • 10 left"
    assert presentation.tone == "positive"


def test_projected_zero_is_not_described_as_realized() -> None:
    presentation = journal_pl_presentation("Bought", 0, None)

    assert presentation.tooltip == "Projected break-even result; not realized."


@pytest.mark.parametrize(
    ("realized_profit", "tone"),
    [(12_345, "positive"), (-12_345, "negative"), (0, "neutral")],
)
def test_realized_profit_tone_follows_its_sign(realized_profit: int, tone: str) -> None:
    sign = "+" if realized_profit > 0 else ""
    assert journal_pl_presentation(
        "Completed", 99_999, realized_profit
    ) == JournalPLPresentation(f"{sign}{realized_profit:,} gp", tone)


def test_realized_partial_profit_includes_remaining_quantity() -> None:
    assert journal_pl_presentation(
        "Partially sold", 99_999, 1_234, remaining_quantity=7
    ) == JournalPLPresentation("+1,234 gp • 7 left", "positive")


def test_realized_profit_takes_precedence_for_cancelled_partial_trade() -> None:
    assert journal_pl_presentation(
        "Cancelled", 99_999, -250, remaining_quantity=3
    ) == JournalPLPresentation("-250 gp • 3 left", "negative")


def test_cancelled_break_even_with_sale_fills_is_described_as_realized() -> None:
    presentation = journal_pl_presentation(
        "Cancelled", 99_999, 0, remaining_quantity=3
    )

    assert presentation.tooltip == "Realized break-even result."


NOW = datetime(2026, 8, 14, 18, 0, 0, tzinfo=UTC)
# 20:00 on 2026-08-14 for a UTC-7 user, which is already 03:00 on the 15th in UTC.
NOW_UTC_MINUS_7 = datetime(2026, 8, 14, 20, 0, 0, tzinfo=timezone(timedelta(hours=-7)))


def test_all_time_period_always_matches_including_missing_timestamp() -> None:
    assert trade_within_period(None, "All time", NOW) is True
    assert trade_within_period("2020-01-01T00:00:00+00:00", "All time", NOW) is True


def test_missing_timestamp_excluded_from_every_specific_period() -> None:
    for period in PERIOD_FILTERS:
        if period == "All time":
            continue
        assert trade_within_period(None, period, NOW) is False


def test_today_matches_same_calendar_day_only() -> None:
    assert trade_within_period("2026-08-14T02:00:00+00:00", "Today", NOW) is True
    assert trade_within_period("2026-08-13T23:59:00+00:00", "Today", NOW) is False


def test_last_24_hours_is_a_rolling_window() -> None:
    assert trade_within_period("2026-08-13T19:00:00+00:00", "Last 24 hours", NOW) is True
    assert trade_within_period("2026-08-13T17:00:00+00:00", "Last 24 hours", NOW) is False
    assert trade_within_period("2026-08-14T18:30:00+00:00", "Last 24 hours", NOW) is False


def test_this_week_starts_on_monday() -> None:
    # 2026-08-14 is a Friday, so Monday 2026-08-10 starts this week.
    assert trade_within_period("2026-08-10T00:00:00+00:00", "This week", NOW) is True
    assert trade_within_period("2026-08-09T23:00:00+00:00", "This week", NOW) is False


def test_this_month_and_this_year_use_calendar_boundaries() -> None:
    assert trade_within_period("2026-08-01T00:00:00+00:00", "This month", NOW) is True
    assert trade_within_period("2026-07-31T23:00:00+00:00", "This month", NOW) is False
    assert trade_within_period("2026-01-01T00:00:00+00:00", "This year", NOW) is True
    assert trade_within_period("2025-12-31T23:00:00+00:00", "This year", NOW) is False


def test_naive_timestamps_are_read_as_utc() -> None:
    """Naive values predate tz-aware storage; the app has only ever written UTC."""
    assert trade_within_period("2026-08-14T02:00:00", "Today", NOW) is True
    # Read as UTC, this is 17:30 on the 14th for a UTC-7 user — still their "today".
    assert trade_within_period("2026-08-15T00:30:00", "Today", NOW_UTC_MINUS_7) is True


def test_unknown_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown period filter"):
        trade_within_period("2026-08-14T00:00:00+00:00", "Decade", NOW)


def test_calendar_periods_use_the_users_day_not_the_utc_day() -> None:
    """Regression: an evening trade must not be filed under tomorrow.

    A UTC-7 user trading at 18:30 local on the 14th has that stored as 01:30 UTC on the
    15th. Comparing UTC dates would push it out of "Today" and into the next month or
    year at those boundaries.
    """
    evening_trade = "2026-08-15T01:30:00+00:00"

    assert trade_within_period(evening_trade, "Today", NOW_UTC_MINUS_7) is True
    assert trade_within_period(evening_trade, "This week", NOW_UTC_MINUS_7) is True
    assert trade_within_period(evening_trade, "This month", NOW_UTC_MINUS_7) is True
    assert trade_within_period(evening_trade, "This year", NOW_UTC_MINUS_7) is True


def test_month_and_year_boundaries_follow_the_users_clock() -> None:
    new_year_eve_local = datetime(2025, 12, 31, 20, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
    # 03:00 UTC on Jan 1 is still 20:00 on Dec 31 where the user is.
    trade = "2026-01-01T03:00:00+00:00"

    assert trade_within_period(trade, "This year", new_year_eve_local) is True
    assert trade_within_period(trade, "This month", new_year_eve_local) is True


def test_in_progress_positions_are_never_filtered_out_by_period() -> None:
    """Regression: a part-sold position's realized profit must not vanish outside 'All time'.

    Its row always shows, so scoping the summary cards differently made the cards
    contradict the rows they summarize.
    """
    for period in PERIOD_FILTERS:
        assert tracked_position_within_period(None, period, NOW) is True


def test_finished_positions_are_still_scoped_by_period() -> None:
    assert tracked_position_within_period("2026-08-14T02:00:00+00:00", "Today", NOW) is True
    assert tracked_position_within_period("2020-01-01T00:00:00+00:00", "Today", NOW) is False
    assert tracked_position_within_period("2020-01-01T00:00:00+00:00", "All time", NOW) is True


@pytest.mark.parametrize("status", ["Listed for sale", "Partially sold"])
def test_a_stale_ask_needs_attention(status: str) -> None:
    """Asking 1,000 while the market now suggests 950 is a 5% gap, over the 2% floor."""
    assert trade_needs_attention(status, 1_000, 950) is True


@pytest.mark.parametrize(
    "status", ["Pending buy", "Bought", "Completed", "Cancelled", "Completed (manual)"]
)
def test_only_actively_listed_statuses_are_flagged(status: str) -> None:
    assert trade_needs_attention(status, 1_000, 500) is False


def test_a_small_gap_under_the_threshold_is_not_flagged() -> None:
    # 1% under: real but not yet worth an alert.
    assert trade_needs_attention("Listed for sale", 1_000, 990) is False


def test_the_threshold_boundary_is_inclusive() -> None:
    assert trade_needs_attention("Listed for sale", 1_000, 980) is True
    assert trade_needs_attention("Listed for sale", 1_000, 981) is False


def test_an_ask_below_market_is_not_flagged() -> None:
    """Asking less than what the market suggests is a good thing, not a stale ask."""
    assert trade_needs_attention("Listed for sale", 1_000, 1_200) is False


def test_no_live_price_cannot_be_flagged() -> None:
    """No current market observation for this item — nothing to compare against."""
    assert trade_needs_attention("Listed for sale", 1_000, None) is False
    assert trade_needs_attention("Listed for sale", 1_000, 0) is False


def test_a_plan_with_no_offer_on_the_grand_exchange_reads_as_planned() -> None:
    """Regression: a cancelled buy leaves the plan behind on purpose, so the row outlives the
    offer that explains it and goes on reading "Pending buy" — as though something were still
    filling."""
    assert journal_display_status("Pending buy", 0, 32_360, frozenset({12_404})) == "Planned"


def test_a_plan_the_grand_exchange_is_holding_an_offer_for_stays_pending() -> None:
    assert (
        journal_display_status("Pending buy", 0, 12_404, frozenset({12_404})) == "Pending buy"
    )


def test_a_pending_buy_with_fills_stays_pending_even_with_no_offer_left() -> None:
    """Something did buy against this. Whatever became of the offer, "Planned" would be a lie."""
    assert journal_display_status("Pending buy", 62, 12_404, frozenset()) == "Pending buy"


def test_nothing_is_relabelled_without_a_live_view_of_the_slots() -> None:
    """RuneLite not connected: every plan would otherwise read as unplaced. "No offer found"
    and "nowhere to look" must not say the same thing."""
    assert journal_display_status("Pending buy", 0, 32_360, None) == "Pending buy"


def test_a_position_with_no_item_id_is_left_alone() -> None:
    """A hand-entered row has nothing to match against a Grand Exchange slot."""
    assert journal_display_status("Pending buy", 0, None, frozenset({12_404})) == "Pending buy"


def test_only_pending_buys_are_relabelled() -> None:
    for status in ("Bought", "Listed for sale", "Partially sold", "Completed", "Supplies"):
        assert journal_display_status(status, 0, 32_360, frozenset()) == status


def test_planned_rows_still_filter_and_count_as_pending_buys() -> None:
    """The label is display only — the stored status is what the filters see, so a planned
    row is still found under "Pending buy" and still counts as an active trade."""
    assert journal_status_matches("Pending buy", "Pending buy")
    assert journal_status_matches("Pending buy", "Active trades")
