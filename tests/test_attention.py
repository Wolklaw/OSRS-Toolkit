"""The "what just changed" rules behind the attention blink, with no widgets involved."""

from __future__ import annotations

from osrs_toolkit.attention import journal_alert_positions, newly_reached
from osrs_toolkit.runelite_sync import TERMINAL_OFFER_STATES


def test_the_first_look_announces_nothing() -> None:
    """Everything already finished when the app opens is where it was, not news."""
    assert newly_reached(None, {1: "BOUGHT", 2: "SOLD"}, TERMINAL_OFFER_STATES) == frozenset()


def test_an_offer_that_has_just_finished_is_announced() -> None:
    before = {0: "BUYING", 1: "SELLING"}
    after = {0: "BOUGHT", 1: "SELLING"}

    assert newly_reached(before, after, TERMINAL_OFFER_STATES) == frozenset({0})


def test_an_offer_that_finished_last_time_is_not_announced_again() -> None:
    """Uncollected offers sit in their slot for as long as the player leaves them, and
    the slots are re-read every three seconds."""
    before = {0: "BOUGHT"}

    assert newly_reached(before, {0: "BOUGHT"}, TERMINAL_OFFER_STATES) == frozenset()


def test_a_slot_that_appears_already_finished_is_announced() -> None:
    """An offer placed on mobile, or restored on login, arrives whole."""
    assert newly_reached({}, {4: "SOLD"}, TERMINAL_OFFER_STATES) == frozenset({4})


def test_a_collected_slot_is_not_announced() -> None:
    """Collecting removes the slot from the plugin's file entirely."""
    assert newly_reached({0: "BOUGHT"}, {}, TERMINAL_OFFER_STATES) == frozenset()


def test_a_finished_buy_is_the_moment_the_journal_row_lights_up() -> None:
    before = {7: "Pending buy"}

    assert journal_alert_positions(before, {7: "Bought"}) == frozenset({7})


def test_a_flip_that_buys_and_sells_between_two_looks_announces_the_sale_too() -> None:
    """Both statuses are asked for separately for exactly this: folding them into one set
    would let "Bought" count as already-arrived and swallow the "Completed" after it."""
    assert journal_alert_positions({7: "Pending buy"}, {7: "Completed"}) == frozenset({7})
    assert journal_alert_positions({7: "Bought"}, {7: "Completed"}) == frozenset({7})


def test_the_stages_in_between_are_left_alone() -> None:
    """Listing stock for sale is the player's own doing; they know where that row is."""
    assert journal_alert_positions({7: "Bought"}, {7: "Listed for sale"}) == frozenset()
    assert journal_alert_positions({7: "Listed for sale"}, {7: "Partially sold"}) == frozenset()


def test_a_position_created_already_bought_is_announced() -> None:
    """An untracked offer that fills in one go makes its journal row at "Bought"."""
    assert journal_alert_positions({}, {9: "Bought"}) == frozenset({9})


def test_a_position_that_stays_put_is_not_announced() -> None:
    assert journal_alert_positions({7: "Bought"}, {7: "Bought"}) == frozenset()
