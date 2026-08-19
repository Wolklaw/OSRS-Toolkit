from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.buy_limits import buy_limit_status
from osrs_toolkit.journal import SyncedItem, SyncedTrade

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def _buy(
    item_id: int,
    item_name: str,
    quantity: int,
    occurred_at: str,
    *,
    unit_value: int = 100,
) -> SyncedTrade:
    return SyncedTrade(
        event_id=f"{item_id}-{occurred_at}",
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(
                flow="received", item_id=item_id, item_name=item_name,
                quantity=quantity, unit_value=unit_value,
            ),
            SyncedItem(flow="given", item_id=995, item_name="Coins", quantity=quantity * unit_value, unit_value=1),
        ),
    )


def _sell(item_id: int, item_name: str, quantity: int, occurred_at: str) -> SyncedTrade:
    return SyncedTrade(
        event_id=f"sell-{item_id}-{occurred_at}",
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="sell",
        metadata={},
        items=(
            SyncedItem(flow="given", item_id=item_id, item_name=item_name, quantity=quantity, unit_value=100),
            SyncedItem(flow="received", item_id=995, item_name="Coins", quantity=quantity * 100, unit_value=1),
        ),
    )


def test_a_buy_within_the_window_shows_remaining_room() -> None:
    trades = [_buy(4_151, "Whip", 5, "2026-08-16T10:00:00+00:00")]

    (status,) = buy_limit_status(trades, {4_151: 8}, NOW)

    assert status.item_name == "Whip"
    assert status.bought_recently == 5
    assert status.remaining == 3


def test_a_fully_bought_out_item_shows_zero_remaining_not_negative() -> None:
    trades = [_buy(4_151, "Whip", 20, "2026-08-16T10:00:00+00:00")]

    (status,) = buy_limit_status(trades, {4_151: 8}, NOW)

    assert status.remaining == 0


def test_multiple_buys_of_the_same_item_accumulate() -> None:
    trades = [
        _buy(4_151, "Whip", 3, "2026-08-16T09:00:00+00:00"),
        _buy(4_151, "Whip", 2, "2026-08-16T11:00:00+00:00"),
    ]

    (status,) = buy_limit_status(trades, {4_151: 8}, NOW)

    assert status.bought_recently == 5


def test_resets_at_uses_the_oldest_buy_still_in_the_window() -> None:
    trades = [
        _buy(4_151, "Whip", 3, "2026-08-16T09:00:00+00:00"),
        _buy(4_151, "Whip", 2, "2026-08-16T11:00:00+00:00"),
    ]

    (status,) = buy_limit_status(trades, {4_151: 8}, NOW)

    assert status.resets_at == "2026-08-16T13:00:00+00:00"


def test_a_buy_older_than_four_hours_is_excluded() -> None:
    trades = [_buy(4_151, "Whip", 5, "2026-08-16T07:00:00+00:00")]

    assert buy_limit_status(trades, {4_151: 8}, NOW) == []


def test_an_item_with_no_known_buy_limit_is_excluded() -> None:
    trades = [_buy(4_151, "Whip", 5, "2026-08-16T10:00:00+00:00")]

    assert buy_limit_status(trades, {}, NOW) == []


def test_sells_do_not_count_toward_the_buy_limit() -> None:
    trades = [_sell(4_151, "Whip", 5, "2026-08-16T10:00:00+00:00")]

    assert buy_limit_status(trades, {4_151: 8}, NOW) == []


def test_most_constrained_items_sort_first() -> None:
    trades = [
        _buy(1, "Nearly maxed", 7, "2026-08-16T10:00:00+00:00"),
        _buy(2, "Plenty of room", 1, "2026-08-16T10:00:00+00:00"),
    ]

    statuses = buy_limit_status(trades, {1: 8, 2: 8}, NOW)

    assert [status.item_name for status in statuses] == ["Nearly maxed", "Plenty of room"]
