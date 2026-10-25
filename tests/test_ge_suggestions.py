from __future__ import annotations

from dataclasses import replace

from osrs_toolkit.ge_suggestions import (
    MAX_SUGGESTIONS,
    GeSuggestion,
    combine,
    position_suggestions,
    watchlist_suggestions,
)
from osrs_toolkit.journal import BuyFill, TrackedTrade
from osrs_toolkit.models import ItemMapping, MarketPoint


def _position(**overrides: object) -> TrackedTrade:
    base = TrackedTrade(
        position_id=1,
        created_at="2026-10-25T10:00:00+00:00",
        item_id=453,
        item_name="Coal",
        quantity=500,
        target_buy=150,
        target_sell=200,
        actual_buy=None,
        actual_sell=None,
        status="Pending buy",
    )
    return replace(base, **overrides)


def _point(item_id: int = 453) -> MarketPoint:
    return MarketPoint(
        item_id=item_id,
        high=210,
        low=140,
        high_time=0,
        low_time=0,
        volume_5m=10,
        volume_1h=100,
        average_high_5m=205,
        average_low_5m=145,
        average_high_1h=208,
        average_low_1h=142,
    )


def test_a_pending_buy_asks_for_what_is_still_to_buy() -> None:
    fill = BuyFill(1, 1, 200, 150)

    [suggestion] = position_suggestions([_position(buy_fills=(fill,))])

    assert (suggestion.side, suggestion.price, suggestion.quantity) == ("buy", 150, 300)


def test_stock_in_hand_is_suggested_for_sale_at_the_asking_price() -> None:
    held = _position(
        status="Listed for sale",
        buy_fills=(BuyFill(2, 1, 500, 150),),
        listed_sell_price=210,
    )

    [suggestion] = position_suggestions([held])

    assert (suggestion.side, suggestion.price, suggestion.quantity) == ("sell", 210, 500)


def test_finished_and_cancelled_positions_are_left_out() -> None:
    done = [_position(status="Completed"), _position(status="Cancelled")]

    assert position_suggestions(done) == []


def test_a_watched_item_is_suggested_up_to_its_remaining_buy_limit() -> None:
    mapping = ItemMapping(453, "Coal", False, 13_000, None)

    suggestions = watchlist_suggestions(
        [453], {453: mapping}, {453: _point()}, owned={}, limit_room={453: 400}
    )

    assert [(s.side, s.quantity) for s in suggestions] == [("buy", 400)]


def test_a_watched_item_already_owned_also_gets_a_sell_suggestion() -> None:
    mapping = ItemMapping(453, "Coal", False, 13_000, None)

    suggestions = watchlist_suggestions(
        [453], {453: mapping}, {453: _point()}, owned={453: 90}, limit_room={}
    )

    assert [(s.side, s.quantity) for s in suggestions] == [("buy", 13_000), ("sell", 90)]


def test_a_watched_item_with_no_buy_room_is_not_suggested_to_buy() -> None:
    mapping = ItemMapping(453, "Coal", False, 13_000, None)

    suggestions = watchlist_suggestions(
        [453], {453: mapping}, {453: _point()}, owned={}, limit_room={453: 0}
    )

    assert suggestions == []


def test_positions_win_over_the_watchlist_and_the_list_is_capped() -> None:
    plan = GeSuggestion(453, "Coal", "buy", 150, 500, "position")
    watched = [GeSuggestion(453, "Coal", "buy", 140, 13_000, "watchlist")] + [
        GeSuggestion(item_id, "Item", "buy", 1, 1, "watchlist") for item_id in range(1, 40)
    ]

    combined = combine([plan], watched)

    assert combined[0] == plan
    assert all(s.price != 140 for s in combined)
    assert len(combined) == MAX_SUGGESTIONS
