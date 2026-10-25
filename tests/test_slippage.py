from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from osrs_toolkit.journal import SyncedItem, SyncedTrade
from osrs_toolkit.slippage import (
    by_item_slippage,
    fill_slippage,
    slippage_fills,
    summarize_slippage,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _fill(side: str, offer_price: object, quantity: int, coins: int, name: str = "Whip"):
    goods = SyncedItem(
        flow="received" if side == "buy" else "given",
        item_id=4151,
        item_name=name,
        quantity=quantity,
        unit_value=coins // quantity,
    )
    money = SyncedItem(
        flow="given" if side == "buy" else "received",
        item_id=995,
        item_name="Coins",
        quantity=coins,
        unit_value=1,
    )
    return SyncedTrade(
        event_id=f"{side}-{offer_price}-{quantity}-{coins}-{name}",
        occurred_at="2026-10-25T10:00:00+00:00",
        event_type="ge_fill",
        account_hash="h",
        account_name="n",
        counterparty=None,
        direction=side,
        metadata={} if offer_price is None else {"offer_price": offer_price},
        items=(money, goods),
    )


def test_a_buy_that_filled_under_the_offer_is_a_saving() -> None:
    fill = fill_slippage(_fill("buy", 1000, 10, 9_800))

    assert fill is not None
    assert fill.improvement == 200


def test_a_sell_that_filled_over_the_ask_is_a_gain() -> None:
    fill = fill_slippage(_fill("sell", 1000, 10, 10_300))

    assert fill is not None
    assert fill.improvement == 300


def test_paying_over_the_offer_or_selling_under_it_is_negative() -> None:
    assert fill_slippage(_fill("buy", 1000, 1, 1_050)).improvement == -50
    assert fill_slippage(_fill("sell", 1000, 1, 950)).improvement == -50


def test_fills_without_an_offer_price_or_that_are_not_ge_fills_are_ignored() -> None:
    assert fill_slippage(_fill("buy", None, 1, 100)) is None
    assert fill_slippage(_fill("buy", True, 1, 100)) is None
    assert fill_slippage(replace(_fill("buy", 100, 1, 100), event_type="player_trade")) is None


def test_the_summary_splits_buys_from_sells() -> None:
    fills = slippage_fills(
        [_fill("buy", 1000, 10, 9_800), _fill("sell", 1000, 10, 10_300)], "All time", NOW
    )

    summary = summarize_slippage(fills)

    assert (summary.buy_improvement, summary.sell_improvement) == (200, 300)
    assert summary.total_improvement == 500
    assert summary.fills == 2


def test_the_period_filter_drops_old_fills() -> None:
    old = replace(_fill("buy", 1000, 1, 900), occurred_at="2026-01-01T00:00:00+00:00")

    assert slippage_fills([old], "Last 7 days", NOW) == []
    assert len(slippage_fills([old], "All time", NOW)) == 1


def test_by_item_hides_items_below_the_minimum_and_ranks_by_size() -> None:
    fills = slippage_fills(
        [_fill("buy", 1000, 1, 990)] * 2
        + [_fill("buy", 1000, 1, 900, "Bones")] * 3
        + [_fill("buy", 1000, 1, 999, "Rune")] * 3,
        "All time",
        NOW,
    )

    rows = by_item_slippage(fills, minimum_fills=3)

    assert [row.label for row in rows] == ["Bones", "Rune"]
    assert rows[0].improvement == 300
    assert round(rows[0].improvement_percent, 1) == 10.0
    assert rows[0].improvement_per_unit == 100
