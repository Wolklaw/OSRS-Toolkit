from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.journal import BuyFill, SaleFill, TrackedTrade, TradeRecord
from osrs_toolkit.savings_goal import (
    SavingsProgress,
    daily_profit_rate,
    estimate_days_remaining,
    realized_profit_since,
)

SINCE = datetime(2026, 8, 10, tzinfo=UTC)


def _completed(completed_at: str, profit: int, *, status: str = "Completed") -> TrackedTrade:
    return TrackedTrade(
        position_id=1,
        created_at=completed_at,
        item_id=1,
        item_name="Whip",
        quantity=1,
        target_buy=1,
        target_sell=2,
        actual_buy=1,
        actual_sell=1 + profit,
        status=status,
        sale_fills=(SaleFill(1, 1, 1, 1 + profit),),
        buy_fills=(BuyFill(1, 1, 1, 1),),
        completed_at=completed_at,
    )


def _still_open(profit: int) -> TrackedTrade:
    """Partially sold: has realized profit, but no completed_at yet."""
    return TrackedTrade(
        position_id=2,
        created_at="2026-08-01T00:00:00+00:00",
        item_id=2,
        item_name="Dragon bones",
        quantity=100,
        target_buy=2_000,
        target_sell=2_500,
        actual_buy=2_000,
        actual_sell=None,
        status="Partially sold",
        sale_fills=(SaleFill(1, 2, 1, 2_000 + profit),),
        buy_fills=(BuyFill(1, 2, 100, 2_000),),
        completed_at=None,
    )


def _supplies(completed_at: str, profit: int) -> TrackedTrade:
    return _completed(completed_at, profit, status="Supplies")


# --- SavingsProgress -----------------------------------------------------------------


def test_percent_is_clamped_at_100() -> None:
    progress = SavingsProgress("Twisted bow", target=1_000, saved=5_000)
    assert progress.percent == 100.0
    assert progress.remaining == 0
    assert progress.is_reached is True


def test_percent_reflects_partial_progress() -> None:
    progress = SavingsProgress("Twisted bow", target=1_000, saved=250)
    assert progress.percent == 25.0
    assert progress.remaining == 750
    assert progress.is_reached is False


# --- realized_profit_since -------------------------------------------------------------


def test_a_completed_position_before_since_is_excluded() -> None:
    tracked = [_completed("2026-08-05T00:00:00+00:00", 1_000)]
    assert realized_profit_since(tracked, [], SINCE) == 0


def test_a_completed_position_after_since_counts() -> None:
    position = _completed("2026-08-12T00:00:00+00:00", 1_000)
    assert realized_profit_since([position], [], SINCE) == position.realized_profit


def test_a_still_open_position_always_counts_its_realized_part() -> None:
    """Mirrors the Journal/Performance rule: a position with no completed_at yet stays in
    scope so its already-realized partial profit is never silently dropped."""
    position = _still_open(500)
    assert realized_profit_since([position], [], SINCE) == position.realized_profit


def test_supplies_positions_never_count() -> None:
    tracked = [_supplies("2026-08-12T00:00:00+00:00", 1_000)]
    assert realized_profit_since(tracked, [], SINCE) == 0


def test_manual_trades_scope_by_recorded_at() -> None:
    before = TradeRecord(1, "2026-08-05T00:00:00+00:00", "Yew logs", 10, 300, 400)
    after = TradeRecord(2, "2026-08-12T00:00:00+00:00", "Yew logs", 10, 300, 400)
    assert realized_profit_since([], [before, after], SINCE) == after.profit


# --- daily_profit_rate / estimate_days_remaining ----------------------------------------


def test_daily_profit_rate_averages_over_the_window() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    position = _completed("2026-08-15T00:00:00+00:00", 7_000)
    assert daily_profit_rate([position], [], now, window_days=7) == position.realized_profit / 7


def test_estimate_days_remaining_projects_from_the_rate() -> None:
    assert estimate_days_remaining(10_000, daily_rate=2_000.0) == 5.0


def test_estimate_days_remaining_is_none_for_a_flat_or_negative_rate() -> None:
    assert estimate_days_remaining(10_000, daily_rate=0.0) is None
    assert estimate_days_remaining(10_000, daily_rate=-500.0) is None


def test_estimate_days_remaining_is_zero_once_the_goal_is_reached() -> None:
    assert estimate_days_remaining(0, daily_rate=0.0) == 0.0
    assert estimate_days_remaining(-500, daily_rate=1_000.0) == 0.0
