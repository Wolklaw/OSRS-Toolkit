from datetime import UTC, datetime

import pytest

from osrs_toolkit.journal_presentation import (
    JOURNAL_STATUS_FILTERS,
    PERIOD_FILTERS,
    JournalPLPresentation,
    journal_pl_presentation,
    journal_status_matches,
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
    )


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


def test_naive_timestamps_are_assumed_to_share_now_timezone() -> None:
    assert trade_within_period("2026-08-14T02:00:00", "Today", NOW) is True


def test_unknown_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown period filter"):
        trade_within_period("2026-08-14T00:00:00+00:00", "Decade", NOW)
