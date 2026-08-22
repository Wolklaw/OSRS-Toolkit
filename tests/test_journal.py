import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.ranking import ge_tax


def test_journal_persists_and_calculates_realized_profit(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    trade_id = repository.add("Test item", 10, 1_000, 1_200)

    trade = repository.list_all()[0]
    assert trade.trade_id == trade_id
    assert trade.tax_each == 24
    assert trade.invested == 10_000
    assert trade.profit == 1_760
    assert trade.roi == pytest.approx(17.6)

    repository.delete(trade_id)
    assert repository.list_all() == []


def test_recommendation_can_be_tracked_through_completion(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Tracked item", 100, 1_000, 1_200)

    pending = repository.list_tracked()[0]
    assert pending.position_id == position_id
    assert pending.status == "Pending buy"
    assert pending.strategy == "Balanced (1–4h)"
    assert pending.estimated_profit == 17_600
    assert pending.realized_profit is None

    repository.update_tracked(position_id, "Completed", 1_010, 1_190)
    completed = repository.list_tracked()[0]
    assert completed.realized_profit == 15_700
    assert completed.sold_quantity == 100
    assert completed.remaining_quantity == 0


def test_tracked_trade_supports_multiple_sale_prices_and_partial_progress(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Variable sale", 10, 1_800, 2_100)

    repository.update_tracked(
        position_id,
        "Partially sold",
        1_800,
        None,
        [(5, 2_002)],
    )
    partial = repository.list_tracked()[0]
    assert partial.sold_quantity == 5
    assert partial.remaining_quantity == 5
    assert partial.average_sell_price == 2_002
    assert partial.realized_profit == 810

    repository.update_tracked(
        position_id,
        "Completed",
        1_800,
        None,
        [(5, 2_002), (5, 1_900)],
    )
    completed = repository.list_tracked()[0]
    assert completed.sold_quantity == 10
    assert completed.remaining_quantity == 0
    assert completed.average_sell_price == 1_951
    assert completed.realized_profit == 1_120


def test_completed_trade_rejects_unaccounted_sale_quantity(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Incomplete sale", 10, 1_800, 2_100)

    with pytest.raises(ValueError, match="full quantity"):
        repository.update_tracked(
            position_id,
            "Completed",
            1_800,
            None,
            [(5, 2_002)],
        )


def test_overnight_trade_preserves_targets_and_records_daily_suggestion(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(
        1,
        "Overnight item",
        10,
        1_800,
        2_100,
        "Overnight (8–12h)",
    )

    repository.review_suggestion(
        position_id,
        1_750,
        2_020,
        "2099-01-02T08:00:00+00:00",
    )
    trade = repository.list_tracked()[0]

    assert trade.target_buy == 1_800
    assert trade.target_sell == 2_100
    assert trade.buy_suggestion == 1_750
    assert trade.sell_suggestion == 2_020
    assert trade.suggestion_was_refreshed is True


def test_legacy_malformed_balanced_strategy_is_normalized(tmp_path: Path) -> None:
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Legacy strategy", 10, 100, 120)
    malformed_strategy = "Balanced (1\u00e2\u20ac\u201c4h)"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET strategy = ? WHERE position_id = ?",
            (malformed_strategy, position_id),
        )

    repaired = JournalRepository(database_path).list_tracked()[0]
    assert repaired.strategy == "Balanced (1–4h)"


def test_a_database_written_before_the_ask_existed_opens_and_asks_its_suggestion(
    tmp_path: Path,
) -> None:
    """Every position already on disk was saved without a recorded ask. Opening one must
    migrate rather than fail, and it has nothing to say about its ask but its own target."""
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.record_listed_price(position_id, 1_640)

    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE tracked_trades DROP COLUMN listed_sell_price")

    reopened = JournalRepository(database_path).list_tracked()[0]
    assert reopened.listed_sell_price is None
    assert reopened.asking_price == 1_800


def test_tracked_trade_supports_multiple_buy_prices(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Variable buy", 10, 1_800, 2_100)

    repository.update_tracked(
        position_id,
        "Bought",
        None,
        None,
        None,
        [(6, 1_700), (4, 1_900)],
    )
    bought = repository.list_tracked()[0]
    assert bought.bought_quantity == 10
    assert bought.average_buy_price == 1_780
    assert bought.actual_buy == 1_780

    repository.update_tracked(
        position_id,
        "Completed",
        None,
        None,
        [(10, 2_050)],
        [(6, 1_700), (4, 1_900)],
    )
    completed = repository.list_tracked()[0]
    assert completed.realized_profit == (2_050 - 1_780 - 41) * 10


def test_completed_trade_rejects_unaccounted_buy_quantity(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Incomplete buy", 10, 1_800, 2_100)

    with pytest.raises(ValueError, match="buy fills"):
        repository.update_tracked(
            position_id,
            "Completed",
            None,
            None,
            [(10, 2_050)],
            [(6, 1_700)],
        )


def test_cancelled_partial_buy_can_be_resized_and_sold(tmp_path: Path) -> None:
    """A buy order cancelled mid-fill leaves a smaller position that must still be sold."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Cancelled partial", 100, 1_500, 1_700)

    # Only 40 of the intended 100 filled before the order was cancelled.
    repository.update_tracked(
        position_id,
        "Bought",
        None,
        None,
        None,
        [(40, 1_520)],
        40,
    )
    resized = repository.list_tracked()[0]
    assert resized.quantity == 40
    assert resized.bought_quantity == 40
    assert resized.average_buy_price == 1_520

    repository.update_tracked(
        position_id,
        "Completed",
        None,
        None,
        [(40, 1_690)],
        [(40, 1_520)],
    )
    completed = repository.list_tracked()[0]
    assert completed.quantity == 40
    assert completed.sold_quantity == 40
    assert completed.realized_profit is not None


def test_the_estimate_prices_the_stock_a_part_bought_position_holds(tmp_path: Path) -> None:
    """Regression: the estimate priced the whole tracked quantity at the price actually paid,
    so a plan of 7,612 that only bought 3,000 advertised 7,612 units of profit — 2.5x what the
    position could possibly make. "Capital traded" already counts only what bought."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.update_tracked(position_id, "Pending buy", None, None, None, [(3_000, 315)])

    trade = repository.list_tracked()[0]

    assert trade.unsold_stock == 3_000
    assert trade.estimated_profit == 3_000 * (337 - 315 - ge_tax(337))


def test_the_estimate_prices_the_whole_plan_while_nothing_has_bought(tmp_path: Path) -> None:
    """Nothing bought yet is a plan, not a shortfall: there is no stock to price instead."""
    repository = JournalRepository(tmp_path / "journal.db")
    repository.track(1, "Shark lure", 7_612, 315, 337)

    trade = repository.list_tracked()[0]

    assert trade.unsold_stock == 7_612
    assert trade.estimated_profit == 7_612 * (337 - 315 - ge_tax(337))


def test_the_estimate_counts_down_as_a_position_sells(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="sell", quantity=4, unit_price=1_800
    )

    trade = repository.list_tracked()[0]

    assert trade.unsold_stock == 6
    assert trade.estimated_profit == 6 * (1_800 - 1_500 - ge_tax(1_800))


def test_quantity_cannot_shrink_below_recorded_fills(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Guarded shrink", 50, 100, 150)

    with pytest.raises(ValueError, match="exceed the tracked quantity"):
        repository.update_tracked(
            position_id,
            "Bought",
            None,
            None,
            None,
            [(30, 110)],
            20,
        )


def test_completed_at_is_set_on_terminal_status_and_cleared_when_reopened(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Terminal timestamps", 10, 100, 150)
    assert repository.list_tracked()[0].completed_at is None

    repository.update_tracked(position_id, "Cancelled", None, None)
    cancelled = repository.list_tracked()[0]
    assert cancelled.completed_at is not None

    repository.update_tracked(position_id, "Bought", None, None, None, [(10, 105)])
    reopened = repository.list_tracked()[0]
    assert reopened.completed_at is None


def test_ge_fill_applies_to_the_oldest_eligible_pending_buy(tmp_path: Path) -> None:
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    older_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    newer_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE position_id = ?",
            (older_id,),
        )
        connection.execute(
            "UPDATE tracked_trades SET created_at = '2026-01-01T00:00:00+00:00' "
            "WHERE position_id = ?",
            (newer_id,),
        )

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=10, unit_price=1_520
    )

    assert matched == older_id
    older = next(t for t in repository.list_tracked() if t.position_id == older_id)
    newer = next(t for t in repository.list_tracked() if t.position_id == newer_id)
    assert older.status == "Bought"
    assert older.average_buy_price == 1_520
    assert newer.status == "Pending buy"
    assert newer.bought_quantity == 0


def test_ge_fill_buy_side_preserves_an_already_recorded_sell_price(tmp_path: Path) -> None:
    """Regression: a buy-side-only update must not blank out an already-recorded sell price."""
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET actual_sell = 1800 WHERE position_id = ?", (position_id,)
        )

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=10, unit_price=1_500
    )

    assert matched == position_id
    trade = repository.list_tracked()[0]
    assert trade.status == "Bought"
    assert trade.actual_sell == 1_800


def test_ge_fill_partial_buy_keeps_position_pending_until_fully_bought(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)

    first = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=6, unit_price=1_500
    )
    assert first == position_id
    midway = repository.list_tracked()[0]
    assert midway.status == "Pending buy"
    assert midway.bought_quantity == 6

    second = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=4, unit_price=1_550
    )
    assert second == position_id
    done = repository.list_tracked()[0]
    assert done.status == "Bought"
    assert done.bought_quantity == 10
    assert done.average_buy_price == 1_520


def test_ge_fill_overshoot_without_total_quantity_leaves_the_existing_position_untouched(
    tmp_path: Path,
) -> None:
    """A fill that overshoots the one eligible position's room, with no total_quantity to
    size a fresh position correctly, is left unmatched rather than guessed at — guessing
    wrong here would create a position that can never reconcile with the real offer."""
    repository = JournalRepository(tmp_path / "journal.db")
    existing_id = repository.track(1, "Whip", 10, 1_500, 1_800)

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=15, unit_price=1_500
    )

    assert matched is None
    existing = repository.list_tracked()[0]
    assert existing.position_id == existing_id
    assert existing.status == "Pending buy"
    assert existing.bought_quantity == 0


def test_ge_fill_sell_side_moves_through_partially_sold_to_completed(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])

    first = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="sell", quantity=4, unit_price=1_800
    )
    assert first == position_id
    partial = repository.list_tracked()[0]
    assert partial.status == "Partially sold"
    # Regression: a sell-side-only update must not blank out the already-recorded buy price.
    assert partial.actual_buy == 1_500

    second = repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="sell", quantity=6, unit_price=1_820
    )
    assert second == position_id
    completed = repository.list_tracked()[0]
    assert completed.status == "Completed"
    assert completed.actual_buy == 1_500
    assert completed.realized_profit is not None


def test_selling_a_part_bought_plan_completes_it_at_what_it_bought(tmp_path: Path) -> None:
    """Regression: a plan bigger than the offer placed against it can never finish buying, so
    its sale used to find nothing eligible and the row sat on "Pending buy" for good — the flip
    was done, the profit realized, and the Journal still showed it as filling."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.apply_offer_opened(
        item_id=1, item_name="Shark lure", side="buy", total_quantity=3_000, offer_price=315
    )
    repository.apply_synced_ge_fill(
        item_id=1,
        item_name="Shark lure",
        side="buy",
        quantity=3_000,
        unit_price=315,
        total_quantity=3_000,
    )
    assert repository.list_tracked()[0].status == "Pending buy"

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Shark lure", side="sell", quantity=3_000, unit_price=337
    )

    assert matched == position_id
    completed = repository.list_tracked()[0]
    assert completed.status == "Completed"
    # Resized to the stock it really held; the 4,612 it never bought are not part of the flip.
    assert completed.quantity == 3_000
    assert (completed.actual_buy, completed.actual_sell) == (315, 337)
    assert completed.realized_profit == 3_000 * (337 - 315 - ge_tax(337))


def test_selling_part_of_a_part_bought_plan_keeps_it_partially_sold(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.update_tracked(position_id, "Pending buy", None, None, None, [(3_000, 315)])

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Shark lure", side="sell", quantity=1_200, unit_price=337
    )

    assert matched == position_id
    partial = repository.list_tracked()[0]
    assert partial.status == "Partially sold"
    assert partial.quantity == 3_000
    assert partial.remaining_quantity == 1_800


def test_a_sale_bigger_than_a_part_bought_plan_holds_stays_unmatched(tmp_path: Path) -> None:
    """Only what a position bought is stock it can sell. A sale covering more than that came
    from somewhere else, and splitting it across positions is the reconciliation this app
    deliberately leaves to the player."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.update_tracked(position_id, "Pending buy", None, None, None, [(1_000, 315)])

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Shark lure", side="sell", quantity=1_500, unit_price=337
    )

    assert matched is None
    untouched = repository.list_tracked()[0]
    assert (untouched.status, untouched.quantity) == ("Pending buy", 7_612)


def test_a_sale_prefers_a_finished_holding_over_an_older_part_bought_plan(
    tmp_path: Path,
) -> None:
    """The part-bought fallback is a last resort: a position that finished buying is the
    obvious owner of a sale, however much older the one still buying is."""
    repository = JournalRepository(tmp_path / "journal.db")
    still_buying = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.update_tracked(still_buying, "Pending buy", None, None, None, [(3_000, 315)])
    bought = repository.track(1, "Shark lure", 500, 320, 340)
    repository.update_tracked(bought, "Bought", None, None, None, [(500, 320)])

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Shark lure", side="sell", quantity=500, unit_price=340
    )

    assert matched == bought
    by_position = {trade.position_id: trade for trade in repository.list_tracked()}
    assert by_position[bought].status == "Completed"
    assert by_position[still_buying].status == "Pending buy"
    assert by_position[still_buying].quantity == 7_612


def test_listing_a_part_bought_plan_resizes_it_to_what_it_bought(tmp_path: Path) -> None:
    """Listing the stock a position gave up buying is the player saying the buying is over,
    so the position becomes the size of that stock and can be sold down to Completed."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Shark lure", 7_612, 315, 337)
    repository.update_tracked(position_id, "Pending buy", None, None, None, [(3_000, 315)])

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Shark lure", side="sell", total_quantity=3_000, offer_price=337
    )

    assert matched == position_id
    listed = repository.list_tracked()[0]
    assert listed.status == "Listed for sale"
    assert listed.quantity == 3_000
    assert listed.actual_buy == 315


def test_ge_fill_buy_with_no_eligible_position_and_no_total_quantity_is_left_unmatched(
    tmp_path: Path,
) -> None:
    """Without the offer's real total_quantity, a fresh fill can't be sized correctly, so
    it is left for the RuneLite activity feed rather than spawning a position that later
    fills of the same offer could never merge back into (see the sibling test below for
    the total_quantity-aware creation path, which is the one that should actually fire)."""
    repository = JournalRepository(tmp_path / "journal.db")

    matched = repository.apply_synced_ge_fill(
        item_id=999, item_name="New item", side="buy", quantity=1, unit_price=100
    )

    assert matched is None
    assert repository.list_tracked() == []


def test_ge_fill_buy_with_no_match_sizes_the_new_position_to_the_offer_total(
    tmp_path: Path,
) -> None:
    """When the plugin reports the GE offer's full total_quantity, the auto-created
    position should use that as its target instead of just the first partial fill, so
    later fills of the same offer keep accumulating onto it rather than each spawning
    their own position."""
    repository = JournalRepository(tmp_path / "journal.db")

    first = repository.apply_synced_ge_fill(
        item_id=2,
        item_name="Blighted teleport spell sack",
        side="buy",
        quantity=7,
        unit_price=57,
        total_quantity=8_000,
    )
    assert first is not None
    midway = repository.list_tracked()[0]
    assert midway.quantity == 8_000
    assert midway.status == "Pending buy"
    assert midway.bought_quantity == 7

    second = repository.apply_synced_ge_fill(
        item_id=2,
        item_name="Blighted teleport spell sack",
        side="buy",
        quantity=30,
        unit_price=59,
        total_quantity=8_000,
    )

    assert second == first
    still_pending = repository.list_tracked()[0]
    assert still_pending.status == "Pending buy"
    assert still_pending.bought_quantity == 37


def test_ge_fill_buy_with_no_match_seeds_the_live_suggestion_as_the_sell_estimate(
    tmp_path: Path,
) -> None:
    """An untracked fill still gets a real profit estimate when a live market suggestion is
    available, instead of one that always reads as guaranteed break-even."""
    repository = JournalRepository(tmp_path / "journal.db")

    position_id = repository.apply_synced_ge_fill(
        item_id=2,
        item_name="Blighted teleport spell sack",
        side="buy",
        quantity=8_000,
        unit_price=57,
        total_quantity=8_000,
        suggested_sell_price=64,
    )

    assert position_id is not None
    created = repository.list_tracked()[0]
    # target_sell still mirrors the buy price: this position must never be mistaken for a
    # plan the player made on purpose (see test_offer_opened_buy_always_creates_its_own_position).
    assert created.target_sell == 57
    assert created.sell_suggestion == 64
    assert created.estimated_profit > 0


def test_ge_fill_buy_with_no_match_ignores_a_suggestion_at_or_below_cost(tmp_path: Path) -> None:
    """A suggestion that would not even cover what was paid is not a suggestion worth
    showing — falls back to the previous break-even behavior instead of a guaranteed loss."""
    repository = JournalRepository(tmp_path / "journal.db")

    repository.apply_synced_ge_fill(
        item_id=2,
        item_name="Blighted teleport spell sack",
        side="buy",
        quantity=8_000,
        unit_price=57,
        total_quantity=8_000,
        suggested_sell_price=57,
    )

    created = repository.list_tracked()[0]
    assert created.sell_suggestion == 57


def test_ge_fill_sell_with_no_eligible_position_returns_none(tmp_path: Path) -> None:
    """Unlike buys, a sell fill with nothing to attach to is left alone rather than
    creating a position — there is no such thing as tracking a sale plan retroactively."""
    repository = JournalRepository(tmp_path / "journal.db")

    matched = repository.apply_synced_ge_fill(
        item_id=999, item_name="New item", side="sell", quantity=1, unit_price=100
    )

    assert matched is None
    assert repository.list_tracked() == []


def test_offer_opened_buy_starts_tracking_before_anything_fills(tmp_path: Path) -> None:
    """Placing a buy offer should show up in the Journal immediately, not only once the
    first fill lands — nothing has been bought yet, but the player has committed to it."""
    repository = JournalRepository(tmp_path / "journal.db")

    position_id = repository.apply_offer_opened(
        item_id=24_615,
        item_name="Blighted teleport spell sack",
        side="buy",
        total_quantity=8_000,
        offer_price=60,
    )

    assert position_id is not None
    created = repository.list_tracked()[0]
    assert created.position_id == position_id
    assert created.quantity == 8_000
    assert created.status == "Pending buy"
    assert created.bought_quantity == 0
    assert created.target_buy == 60


def test_offer_opened_buy_always_creates_its_own_position(tmp_path: Path) -> None:
    """Two independent buy offers for the same item (e.g. two GE slots) should not be
    conflated into one position — each offer gets its own."""
    repository = JournalRepository(tmp_path / "journal.db")

    first = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=5, offer_price=1_500
    )
    second = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=10, offer_price=1_600
    )

    assert first != second
    assert {trade.position_id for trade in repository.list_tracked()} == {first, second}


def test_offer_opened_buy_with_no_plan_seeds_the_live_suggestion_as_the_sell_estimate(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")

    position_id = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        suggested_sell_price=1_650,
    )

    assert position_id is not None
    created = repository.list_tracked()[0]
    assert created.target_sell == 1_500
    assert created.sell_suggestion == 1_650
    assert created.estimated_profit > 0


def test_offer_opened_buy_seeded_suggestion_does_not_get_treated_as_a_plan(
    tmp_path: Path,
) -> None:
    """Regression: seeding current_sell_suggestion above the offer price must not make an
    auto-created position look like something the player planned on purpose — the "planned"
    check only ever looks at target_sell, so a second, genuinely independent offer for the
    same item must still get its own row instead of merging into the first."""
    repository = JournalRepository(tmp_path / "journal.db")

    first = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        suggested_sell_price=1_650,
    )
    second = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=5, offer_price=1_520
    )

    assert first != second
    assert {trade.position_id for trade in repository.list_tracked()} == {first, second}


def test_offer_opened_buy_adopts_the_plan_the_player_already_tracked(tmp_path: Path) -> None:
    """Tracking a suggested flip and then placing that very offer is the ordinary workflow. It
    must not leave a duplicate row priced from the offer, whose sell target equals its buy
    target and so always shows a loss — the tracked plan's own targets are the point."""
    repository = JournalRepository(tmp_path / "journal.db")
    planned = repository.track(24_615, "Iorwerth camp teleport", 77, 6_479, 6_896)

    matched = repository.apply_offer_opened(
        item_id=24_615,
        item_name="Iorwerth camp teleport",
        side="buy",
        total_quantity=77,
        offer_price=6_479,
    )

    assert matched == planned
    positions = repository.list_tracked()
    assert len(positions) == 1
    assert positions[0].target_sell == 6_896
    assert positions[0].quantity == 77
    assert positions[0].estimated_profit > 0


def test_offer_opened_buy_grows_a_plan_when_the_offer_is_larger(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    planned = repository.track(1, "Whip", 5, 1_500, 1_800)

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=12, offer_price=1_500
    )

    assert matched == planned
    position = repository.list_tracked()[0]
    assert position.quantity == 12
    # The player's planned prices survive being resized.
    assert (position.target_buy, position.target_sell) == (1_500, 1_800)


def test_offer_opened_buy_leaves_a_smaller_offer_room_in_the_plan(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    repository.track(1, "Whip", 10, 1_500, 1_800)

    repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=4, offer_price=1_500
    )

    assert repository.list_tracked()[0].quantity == 10


def test_offer_opened_buy_rejoins_the_part_bought_position_it_matches(tmp_path: Path) -> None:
    """Regression: the game re-sends every live offer on a world hop, and an older plugin build
    cannot say so. An offer matching a part-bought position on both size and price is that same
    offer announced again, not a second one — believing otherwise duplicated an active flip
    every time the player hopped."""
    repository = JournalRepository(tmp_path / "journal.db")
    planned = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(planned, "Pending buy", None, None, None, [(4, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=10, offer_price=1_500
    )

    assert matched == planned
    positions = repository.list_tracked()
    assert len(positions) == 1
    # Adopting must not disturb what already bought, nor the targets the player set.
    assert positions[0].bought_quantity == 4
    assert (positions[0].target_buy, positions[0].target_sell) == (1_500, 1_800)


def test_offer_opened_buy_beside_a_part_bought_position_it_differs_from_gets_its_own_row(
    tmp_path: Path,
) -> None:
    """The match has to be exact to read as a re-announcement. An offer at another price is a
    separate commitment however much the first one has bought."""
    repository = JournalRepository(tmp_path / "journal.db")
    planned = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(planned, "Pending buy", None, None, None, [(4, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=10, offer_price=1_520
    )

    assert matched != planned
    assert len(repository.list_tracked()) == 2


def test_restored_offer_rejoins_the_position_tracking_it_whatever_its_price(
    tmp_path: Path,
) -> None:
    """A restored offer is one already running, so it cannot be a new commitment: it belongs to
    whatever is tracking it even when the plan was made at a different price to the one the
    offer was finally placed at."""
    repository = JournalRepository(tmp_path / "journal.db")
    planned = repository.track(1, "Whip", 10, 1_600, 1_800)
    repository.update_tracked(planned, "Pending buy", None, None, None, [(4, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        restored=True,
    )

    assert matched == planned
    assert len(repository.list_tracked()) == 1


def test_restored_offer_prefers_the_position_sized_to_it(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    older = repository.track(1, "Whip", 25, 1_500, 1_800)
    repository.update_tracked(older, "Pending buy", None, None, None, [(4, 1_500)])
    same_size = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(same_size, "Pending buy", None, None, None, [(2, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        restored=True,
    )

    assert matched == same_size
    assert len(repository.list_tracked()) == 2


def test_restored_offer_prefers_the_position_tracking_it_over_an_untouched_plan(
    tmp_path: Path,
) -> None:
    """Regression: a re-sent offer cannot be the placing of a plan that was never placed, but
    it matched one first — adopting a flip it had nothing to do with and resizing it to the
    offer, while the position actually buying against that offer was left behind. On a world
    hop it happened again to whatever plan was untouched."""
    repository = JournalRepository(tmp_path / "journal.db")
    buying = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=10, offer_price=1_500
    )
    repository.apply_synced_ge_fill(
        item_id=1, item_name="Whip", side="buy", quantity=4, unit_price=1_500, total_quantity=10
    )
    plan = repository.track(1, "Whip", 6, 1_500, 1_800)

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        restored=True,
    )

    assert matched == buying
    by_position = {trade.position_id: trade for trade in repository.list_tracked()}
    # The plan is untouched: neither adopted nor grown to an offer that was not placed for it.
    assert by_position[plan].quantity == 6
    assert by_position[plan].bought_quantity == 0
    assert repository.list_tracked() == list(by_position.values())


def test_a_newly_placed_offer_still_takes_the_plan_it_was_placed_for(tmp_path: Path) -> None:
    """The other side of the same rule: a new offer is a plan being acted on, so the plan
    wins there even while another position for the item is part way through buying."""
    repository = JournalRepository(tmp_path / "journal.db")
    buying = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(buying, "Pending buy", None, None, None, [(4, 1_500)])
    plan = repository.track(1, "Whip", 6, 1_500, 1_800)

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="buy", total_quantity=6, offer_price=1_500
    )

    assert matched == plan


def test_restored_offer_with_nothing_tracked_still_starts_a_position(tmp_path: Path) -> None:
    """Offers placed on mobile, or while this app was closed, arrive as restored ones. Nothing
    is tracking them yet, so they still have to open a row of their own."""
    repository = JournalRepository(tmp_path / "journal.db")

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        restored=True,
    )

    assert matched is not None
    assert len(repository.list_tracked()) == 1


def test_restored_offer_does_not_reclaim_a_position_that_finished_buying(
    tmp_path: Path,
) -> None:
    """A restored buy offer adopts what is still buying. A position already bought in full is
    waiting on a sale, and pulling it back would undo that."""
    repository = JournalRepository(tmp_path / "journal.db")
    bought = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(bought, "Bought", None, None, None, [(10, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="buy",
        total_quantity=10,
        offer_price=1_500,
        restored=True,
    )

    assert matched != bought
    by_id = {trade.position_id: trade for trade in repository.list_tracked()}
    assert by_id[bought].status == "Bought"


def test_restored_sell_offer_counts_an_already_listed_position_as_matched(
    tmp_path: Path,
) -> None:
    """The listing this offer describes is already on the page. Reporting nothing matched would
    make a restored sell look unhandled on every login."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Listed for sale", None, None, None, [(10, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="sell",
        total_quantity=10,
        offer_price=1_800,
        restored=True,
    )

    assert matched == position_id
    assert repository.list_tracked()[0].status == "Listed for sale"


def test_offer_opened_sell_advances_a_bought_position_to_listed_for_sale(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="sell", total_quantity=10, offer_price=1_800
    )

    assert matched == position_id
    trade = repository.list_tracked()[0]
    assert trade.status == "Listed for sale"
    assert trade.actual_buy == 1_500


def test_offer_opened_sell_with_nothing_bought_does_nothing(tmp_path: Path) -> None:
    """Unlike a buy, a sell offer can't start tracking on its own — there is no plan to
    advance without something already bought behind it."""
    repository = JournalRepository(tmp_path / "journal.db")

    matched = repository.apply_offer_opened(
        item_id=999, item_name="New item", side="sell", total_quantity=1, offer_price=100
    )

    assert matched is None
    assert repository.list_tracked() == []


def _synced_trade(event_id: str, occurred_at: str = "2026-08-14T12:00:00+00:00") -> SyncedTrade:
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(
                flow="received", item_id=4151, item_name="Whip", quantity=1, unit_value=1_500
            ),
            SyncedItem(flow="given", item_id=995, item_name="Coins", quantity=1_500, unit_value=1),
        ),
    )


def test_add_synced_trades_batches_inserts_and_reports_duplicates(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    event_id = "11111111-1111-1111-1111-111111111111"
    other_id = "22222222-2222-2222-2222-222222222222"
    trade_one = _synced_trade(event_id)
    trade_two = _synced_trade(other_id)

    results = repository.add_synced_trades([trade_one, trade_two, trade_one])
    assert results == [True, True, False]

    fetched = repository.get_synced_trade(event_id)
    assert fetched is not None
    assert len(fetched.items) == 2
    assert repository.get_synced_trade("missing-event") is None
    assert len(repository.list_synced_trades()) == 2


def test_default_journal_recovers_from_version_independent_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_data = tmp_path / "local"
    roaming_data = tmp_path / "roaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_data))
    monkeypatch.setenv("APPDATA", str(roaming_data))

    repository = JournalRepository()
    repository.add("Preserved item", 5, 100, 150)

    # A later launch snapshots the populated database outside the install/data location.
    JournalRepository()
    database_path = local_data / "OSRSToolkit" / "data" / "toolkit.db"
    database_path.unlink()

    recovered = JournalRepository()
    assert recovered.list_all()[0].item_name == "Preserved item"
    assert list((roaming_data / "OSRSToolkit" / "backups").glob("toolkit-*.db"))


def test_update_tracked_can_move_a_position_to_supplies(tmp_path: Path) -> None:
    """Marking a position 'Supplies' is just an ordinary status change — the same
    ``update_tracked`` call the Update dialog already makes for every other status."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Lobster", 100, 150, 150)

    repository.update_tracked(position_id, "Supplies", None, None)

    assert repository.list_tracked()[0].status == "Supplies"


def test_a_supplies_position_still_absorbs_its_own_remaining_buy_fills(tmp_path: Path) -> None:
    """Regression: reclassifying a still-in-progress position to 'Supplies' must not stop
    the fill matcher from seeing it, or the offer's next fill would spawn a duplicate row."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Lobster", 100, 150, 150)
    repository.update_tracked(position_id, "Supplies", None, None)

    repository.apply_synced_ge_fill(
        item_id=1, item_name="Lobster", side="buy", quantity=60, unit_price=150
    )
    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Lobster", side="buy", quantity=40, unit_price=150
    )

    assert matched == position_id
    trades = repository.list_tracked()
    assert len(trades) == 1
    assert trades[0].status == "Supplies"
    assert trades[0].bought_quantity == 100


def test_a_supplies_position_still_absorbs_its_own_remaining_sell_fills(tmp_path: Path) -> None:
    """A supplies item that does get partly sold off keeps its status — it stays excluded
    from performance grading even though it now has realized proceeds."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Lobster", 100, 150, 150)
    repository.update_tracked(position_id, "Supplies", None, None, None, [(100, 150)])

    matched = repository.apply_synced_ge_fill(
        item_id=1, item_name="Lobster", side="sell", quantity=30, unit_price=150
    )

    assert matched == position_id
    trade = repository.list_tracked()[0]
    assert trade.status == "Supplies"
    assert trade.sold_quantity == 30


def test_cancelling_a_part_filled_supplies_buy_keeps_it_supplies(tmp_path: Path) -> None:
    """apply_offer_cancelled's ordinary resize-to-what-bought path must not pull a position
    back into the flip lifecycle just because its offer got cancelled."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Lobster", 100, 150, 150)
    repository.update_tracked(position_id, "Supplies", None, None, None, [(40, 150)])

    matched = repository.apply_offer_cancelled(
        item_id=1, side="buy", total_quantity=100, offer_price=150
    )

    assert matched == position_id
    trade = repository.list_tracked()[0]
    assert trade.status == "Supplies"
    assert trade.quantity == 40


def _snapshot(account_hash: str, captured_at: str, bank_value: int) -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash=account_hash,
        account_name="Tester",
        captured_at=captured_at,
        equipment=(),
        inventory=(),
        bank=(LoadoutItem(item_id=1, item_name="Coins", quantity=bank_value, unit_value=1),),
        skills={},
    )


def test_loadout_snapshot_total_value_sums_every_slot() -> None:
    snapshot = LoadoutSnapshot(
        account_hash="hash",
        account_name="Tester",
        captured_at="2026-08-15T00:00:00+00:00",
        equipment=(LoadoutItem(1, "Rune scimitar", 1, 15_000),),
        inventory=(LoadoutItem(995, "Coins", 100_000, 1),),
        bank=(LoadoutItem(11_802, "Armadyl godsword", 1, 40_000_000),),
        skills={},
    )

    assert snapshot.total_value == 15_000 + 100_000 + 40_000_000


def test_save_loadout_snapshot_records_net_worth_history(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")

    repository.save_loadout_snapshot(_snapshot("hash", "2026-08-15T00:00:00+00:00", 1_000))
    repository.save_loadout_snapshot(_snapshot("hash", "2026-08-16T00:00:00+00:00", 2_000))

    history = repository.list_net_worth_history()
    assert [point.total_value for point in history] == [1_000, 2_000]
    assert [point.captured_at for point in history] == [
        "2026-08-15T00:00:00+00:00",
        "2026-08-16T00:00:00+00:00",
    ]


def test_net_worth_history_survives_the_latest_snapshot_being_overwritten(
    tmp_path: Path,
) -> None:
    """save_loadout_snapshot replaces the 'current state' row for an account, but each call
    must still leave its own historical reading behind."""
    repository = JournalRepository(tmp_path / "journal.db")

    repository.save_loadout_snapshot(_snapshot("hash", "2026-08-15T00:00:00+00:00", 1_000))
    repository.save_loadout_snapshot(_snapshot("hash", "2026-08-16T00:00:00+00:00", 2_000))

    assert repository.get_latest_loadout_snapshot().captured_at == "2026-08-16T00:00:00+00:00"
    assert len(repository.list_net_worth_history()) == 2


def test_net_worth_history_is_pruned_per_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("osrs_toolkit.journal.MAX_NET_WORTH_HISTORY_PER_ACCOUNT", 3)
    repository = JournalRepository(tmp_path / "journal.db")

    for day in range(1, 6):
        repository.save_loadout_snapshot(
            _snapshot("hash", f"2026-08-{day:02d}T00:00:00+00:00", day * 1_000)
        )

    history = repository.list_net_worth_history()
    assert len(history) == 3
    assert [point.total_value for point in history] == [3_000, 4_000, 5_000]


def test_cancelling_a_buy_keeps_the_plan_the_offer_adopted(tmp_path: Path) -> None:
    """Tracking a suggested flip and then placing exactly that offer means the offer adopts
    the plan instead of opening a row beside it (see apply_offer_opened). Cancelling the
    offer must not then delete the plan, which the player made and can still act on."""
    repository = JournalRepository(tmp_path / "journal.db")
    planned_id = repository.track(453, "Coal", 500, 100, 120)
    assert repository.apply_offer_opened(453, "Coal", "buy", 500, 100) == planned_id

    matched = repository.apply_offer_cancelled(
        item_id=453, side="buy", total_quantity=500, offer_price=100
    )

    assert matched is None
    plan = repository.list_tracked()[0]
    assert plan.position_id == planned_id
    assert plan.status == "Pending buy"
    assert (plan.quantity, plan.target_buy, plan.target_sell) == (500, 100, 120)


def test_editing_a_finished_position_keeps_the_time_it_finished(tmp_path: Path) -> None:
    """Correcting a fill price on old history is an edit, not a second completion. Re-stamping
    it would drag the trade into today's period filters and stretch its recorded hold time."""
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Backdated flip", 10, 100, 150)
    repository.update_tracked(position_id, "Completed", None, None, [(10, 150)], [(10, 100)])
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET completed_at = ? WHERE position_id = ?",
            ("2026-07-01T18:00:00+00:00", position_id),
        )

    repository.update_tracked(position_id, "Completed", None, None, [(10, 151)], [(10, 100)])

    corrected = repository.list_tracked()[0]
    assert corrected.completed_at == "2026-07-01T18:00:00+00:00"
    assert corrected.average_sell_price == 151


def test_reopening_and_finishing_again_records_the_new_completion(tmp_path: Path) -> None:
    """Keeping the original stamp only applies to a position that was terminal already."""
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Reopened flip", 10, 100, 150)
    repository.update_tracked(position_id, "Completed", None, None, [(10, 150)], [(10, 100)])
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET completed_at = ? WHERE position_id = ?",
            ("2026-07-01T18:00:00+00:00", position_id),
        )

    repository.update_tracked(position_id, "Bought", None, None, [], [(10, 100)])
    assert repository.list_tracked()[0].completed_at is None

    repository.update_tracked(position_id, "Completed", None, None, [(10, 150)], [(10, 100)])
    assert repository.list_tracked()[0].completed_at != "2026-07-01T18:00:00+00:00"


def test_listing_synced_trades_since_a_cutoff_drops_older_events(tmp_path: Path) -> None:
    """The buy-limit view re-reads on a timer and only ever looks at the last few hours, so
    it must not pay for the whole imported history — items included."""
    repository = JournalRepository(tmp_path / "journal.db")
    now = datetime.now(UTC)
    repository.add_synced_trades(
        [
            _synced_fill("recent", now - timedelta(hours=1)),
            _synced_fill("ancient", now - timedelta(days=400)),
        ]
    )

    recent = repository.list_synced_trades("ge_fill", since=now - timedelta(hours=4))

    assert [trade.event_id for trade in recent] == ["recent"]
    assert [item.item_name for item in recent[0].items] == ["Coins", "Coal"]
    assert len(repository.list_synced_trades("ge_fill")) == 2


def test_listing_synced_trades_since_a_cutoff_keeps_events_written_without_seconds(
    tmp_path: Path,
) -> None:
    """The plugin's writer omits time fields that are zero, so "12:00Z" sorts after
    "12:00:30Z" as text. The cutoff has to be loose enough that text order cannot lose one."""
    repository = JournalRepository(tmp_path / "journal.db")
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    just_inside = _synced_fill("terse", now - timedelta(hours=3))
    repository.add_synced_trades([replace(just_inside, occurred_at="2026-08-16T09:00Z")])

    recent = repository.list_synced_trades("ge_fill", since=now - timedelta(hours=4))

    assert [trade.event_id for trade in recent] == ["terse"]


def _synced_fill(event_id: str, occurred_at: datetime) -> SyncedTrade:
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at.isoformat(timespec="seconds"),
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(flow="given", item_id=995, item_name="Coins", quantity=5_000, unit_value=1),
            SyncedItem(flow="received", item_id=453, item_name="Coal", quantity=50, unit_value=100),
        ),
    )


def test_a_sell_offer_records_the_price_the_player_actually_listed_at(tmp_path: Path) -> None:
    """Regression: the price on a sell offer was read for its side and then discarded, so a
    position's ask stayed whatever the flip was planned to sell at. Relisting lower changed
    nothing the app could see."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Bought", 1_500, None, None, [(10, 1_500)])

    repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="sell", total_quantity=10, offer_price=1_640
    )

    position = repository.list_tracked()[0]
    assert position.status == "Listed for sale"
    assert position.asking_price == 1_640
    # The plan and the app's own advice are untouched by the player choosing a price.
    assert position.target_sell == 1_800
    assert position.sell_suggestion == 1_800
    assert position.suggestion_was_refreshed is False


def test_relisting_at_a_new_price_replaces_the_recorded_ask(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Bought", 1_500, None, None, [(10, 1_500)])
    repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="sell", total_quantity=10, offer_price=1_800
    )

    repository.apply_offer_cancelled(item_id=1, side="sell", total_quantity=10, offer_price=1_800)
    repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="sell", total_quantity=10, offer_price=1_560
    )

    assert repository.list_tracked()[0].asking_price == 1_560


def test_a_position_never_listed_through_an_offer_asks_its_sell_suggestion(
    tmp_path: Path,
) -> None:
    """Nothing has told the app what this position is asking, so its own target is the best
    statement of intent there is — the behaviour every position had before the ask existed."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Listed for sale", 1_500, None, None, [(10, 1_500)])

    position = repository.list_tracked()[0]
    assert position.listed_sell_price is None
    assert position.asking_price == 1_800


def test_a_restored_sell_offer_records_the_ask_of_an_already_listed_position(
    tmp_path: Path,
) -> None:
    """A world hop re-sends the offer rather than the player placing it. It is still the
    truest statement of the ask available, and for a position listed while the app was
    closed it is the only one."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(position_id, "Listed for sale", 1_500, None, None, [(10, 1_500)])

    matched = repository.apply_offer_opened(
        item_id=1,
        item_name="Whip",
        side="sell",
        total_quantity=10,
        offer_price=1_560,
        restored=True,
    )

    assert matched == position_id
    assert repository.list_tracked()[0].asking_price == 1_560


def test_relisting_a_part_sold_position_records_the_new_ask(tmp_path: Path) -> None:
    """A cancelled sell leaves a part-sold position exactly as it was, so a relist has no
    status to change — and the price on it used to go nowhere, leaving the position asking
    whatever it was asking before the player thought better of it."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Whip", 10, 1_500, 1_800)
    repository.update_tracked(
        position_id, "Partially sold", 1_500, None, [(4, 1_800)], [(10, 1_500)]
    )

    matched = repository.apply_offer_opened(
        item_id=1, item_name="Whip", side="sell", total_quantity=6, offer_price=1_610
    )

    assert matched == position_id
    position = repository.list_tracked()[0]
    assert position.asking_price == 1_610
    # Relisting the remainder is not a sale, a cancellation, or a status change.
    assert position.status == "Partially sold"
    assert position.sold_quantity == 4
    assert position.quantity == 10


def test_a_sell_offer_for_an_untracked_item_still_matches_nothing(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    repository.track(2, "Bow", 10, 1_500, 1_800)

    assert (
        repository.apply_offer_opened(
            item_id=1, item_name="Whip", side="sell", total_quantity=6, offer_price=1_610
        )
        is None
    )
