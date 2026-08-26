"""Drives the real MainWindow's answer to "what am I supposed to type in here?".

While the Grand Exchange is buying or selling something, the journal picks out the one figure
that trade is working towards — the buy price for a buy, the ask for a sale — on every row for
that item, and nothing else. It holds for the whole trade: from the "Set up offer" box opening
to the slot being collected, whether or not the player is still standing at the Grand Exchange
while it fills. These tests drive it from the plugin's own files outwards, the way
``test_ge_offers_render.py`` does, so what is asserted is what the table would really show.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtGui import QBrush

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal_presentation import live_price_highlights, next_move
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter

_REVENANT = 21_802
_WHIP = 4_151

# Journal columns: the price each side of a trade is working towards.
_BUY_PRICE = 4
_SELL_PRICE = 6


def _connect(window: MainWindow, tmp_path: Path) -> Path:
    root = tmp_path / "sync"
    (root / "state").mkdir(parents=True)
    (root / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": True,
                "account_hash": "abc123",
                "account_name": "Example Player",
            }
        ),
        encoding="utf-8",
    )
    window._sync_importer = RuneLiteSyncImporter(root)
    return root


def _open_offer_box(
    root: Path,
    item_id: int = _REVENANT,
    item_name: str | None = "Revenant cave teleport",
    side: str | None = "buy",
    age: timedelta = timedelta(0),
) -> None:
    (root / "state" / "abc123-screen.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": (datetime.now(UTC) - age).isoformat(),
                "item_id": item_id,
                "item_name": item_name,
                "side": side,
            }
        ),
        encoding="utf-8",
    )


def _stand_at_the_grand_exchange(root: Path) -> None:
    """The interface open with no box up: watching an offer fill, or collecting one."""
    _open_offer_box(root, item_id=0, item_name=None, side=None)


def _walk_away(root: Path) -> None:
    """What the plugin does when the interface closes: the file goes away."""
    (root / "state" / "abc123-screen.json").unlink()


def _slot(
    slot: int, item_id: int = _REVENANT, state: str = "BUYING", name: str = "Revenant cave teleport"
) -> dict[str, object]:
    return {
        "slot": slot,
        "itemId": item_id,
        "itemName": name,
        "offerPrice": 606,
        "totalQuantity": 7_788,
        "quantityFilled": 100,
        "spentGp": 60_600,
        "state": state,
    }


def _place_offer(
    root: Path, item_id: int = _REVENANT, state: str = "BUYING", slot: int = 0
) -> None:
    (root / "state" / "abc123.json").write_text(
        json.dumps({str(slot): _slot(slot, item_id, state)}), encoding="utf-8"
    )


def _collect_everything(root: Path) -> None:
    (root / "state" / "abc123.json").write_text(json.dumps({}), encoding="utf-8")


def _marked(window: MainWindow, column: int) -> set[str]:
    """The item names whose ``column`` cell is picked out in the live-offer colour."""
    table = window.journal_table
    return {
        table.item(row, 2).text()
        for row in range(table.rowCount())
        if table.item(row, column).foreground().color().name() == window._live_offer_color
    }


def _bold_columns(window: MainWindow, item_name: str) -> set[int]:
    table = window.journal_table
    for row in range(table.rowCount()):
        if table.item(row, 2).text() != item_name:
            continue
        return {
            column for column in range(table.columnCount()) if table.item(row, column).font().bold()
        }
    raise AssertionError(f"No row for {item_name}")


def _list_for_sale(window: MainWindow, position_id: int) -> None:
    window._journal.update_tracked(position_id, "Listed for sale", None, None, None, [])


# --- the price, and only the price ---------------------------------------------------------


def test_an_open_buy_box_picks_out_the_buy_price(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)

    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}
    assert _BUY_PRICE in _bold_columns(window, "Revenant cave teleport")


def test_nothing_but_the_price_is_marked(window: MainWindow, tmp_path: Path) -> None:
    """The mark answers "what do I type", so it goes on that one number. The Status cell is
    bold on every row already and is not what this is about."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)

    window._refresh_live_offers()

    assert _bold_columns(window, "Revenant cave teleport") == {1, _BUY_PRICE}


def test_the_row_itself_is_left_unwashed(window: MainWindow, tmp_path: Path) -> None:
    """The background belongs to the blink, which is a different thing being said."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)

    window._refresh_live_offers()

    table = window.journal_table
    # A null brush is what leaves the row to the table's own alternating colours.
    assert all(
        table.item(0, column).background() == QBrush() for column in range(table.columnCount())
    )


def test_selling_points_at_the_ask_not_the_buy_price(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    position_id = window._journal.track(_REVENANT, "Revenant cave teleport", 500, 642, 672)
    _list_for_sale(window, position_id)
    _open_offer_box(root, side="sell")

    window._refresh_live_offers()

    bold = _bold_columns(window, "Revenant cave teleport")
    assert _SELL_PRICE in bold
    assert _BUY_PRICE not in bold


def test_a_row_for_a_different_item_is_left_alone(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(_WHIP, "Abyssal whip", 1, 1_500, 1_700)
    _open_offer_box(root)

    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}


def test_both_rows_for_one_item_are_marked(window: MainWindow, tmp_path: Path) -> None:
    """Two pending buys for the same item is ordinary, and picking one would be a guess."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(_REVENANT, "Revenant cave teleport", 4_807, 624, 673)
    _open_offer_box(root)

    window._refresh_live_offers()

    table = window.journal_table
    marked = [
        table.item(row, 3).text()
        for row in range(table.rowCount())
        if table.item(row, _BUY_PRICE).foreground().color().name() == window._live_offer_color
    ]
    assert sorted(marked) == ["4,807", "7,788"]


def test_a_box_the_plugin_stopped_stamping_stops_being_believed(
    window: MainWindow, tmp_path: Path
) -> None:
    """A client killed with the interface up deletes nothing on its way out."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root, age=timedelta(minutes=10))

    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == set()


# --- for the whole trade, not just while you are looking at it -----------------------------


def test_the_mark_holds_after_the_offer_is_confirmed(window: MainWindow, tmp_path: Path) -> None:
    """Confirming closes the box but not the trade: the offer is now filling in front of you."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)
    window._refresh_live_offers()

    _place_offer(root)
    _stand_at_the_grand_exchange(root)
    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}


def test_the_mark_holds_after_walking_away_from_the_grand_exchange(
    window: MainWindow, tmp_path: Path
) -> None:
    """The trade is still happening while the player is off doing something else, and the
    row is still the answer to "what did I place this at?" when they check the app."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _place_offer(root)
    _stand_at_the_grand_exchange(root)
    window._refresh_live_offers()

    _walk_away(root)
    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}


def test_collecting_the_offer_puts_the_mark_out(window: MainWindow, tmp_path: Path) -> None:
    """Collecting empties a slot without producing an event, so the poll has to notice it."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _place_offer(root, state="BOUGHT")
    _stand_at_the_grand_exchange(root)
    window._refresh_live_offers()
    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}

    _collect_everything(root)
    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == set()


def test_a_plan_that_was_never_placed_is_left_alone(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(_WHIP, "Abyssal whip", 1, 1_500, 1_700)
    _place_offer(root)
    _stand_at_the_grand_exchange(root)

    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}


def test_opening_a_box_does_not_drop_the_other_slots(window: MainWindow, tmp_path: Path) -> None:
    """The old highlight jumped to whichever screen was up, so opening a box on one item
    dropped every other offer that was still filling. They are all still happening."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    whip = window._journal.track(_WHIP, "Abyssal whip", 1, 1_500, 1_700)
    _list_for_sale(window, whip)
    (root / "state" / "abc123.json").write_text(
        json.dumps(
            {
                "0": _slot(0, _REVENANT, "BUYING"),
                "1": _slot(1, _WHIP, "SELLING", "Abyssal whip"),
            }
        ),
        encoding="utf-8",
    )
    _stand_at_the_grand_exchange(root)
    window._refresh_live_offers()
    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}
    assert _marked(window, _SELL_PRICE) == {"Abyssal whip"}

    _open_offer_box(root)
    window._refresh_live_offers()

    assert _marked(window, _BUY_PRICE) == {"Revenant cave teleport"}
    assert _marked(window, _SELL_PRICE) == {"Abyssal whip"}


def test_a_ready_to_list_row_keeps_its_own_ask_colour(window: MainWindow, tmp_path: Path) -> None:
    """That colour says whether the ask clears what was paid, which this mark cannot."""
    root = _connect(window, tmp_path)
    position_id = window._journal.track(_REVENANT, "Revenant cave teleport", 500, 642, 672)
    window._journal.update_tracked(position_id, "Bought", 642, None, None, [])
    _open_offer_box(root, side="sell")

    window._refresh_live_offers()

    ask = window.journal_table.item(0, _SELL_PRICE)
    assert ask.font().bold()
    assert ask.foreground().color().name() != window._live_offer_color
    assert "selling this item right now" in ask.toolTip()


# --- which price an offer is about ---------------------------------------------------------


def test_next_move_is_the_side_a_position_still_owes() -> None:
    assert next_move("Pending buy") == "buy"
    assert next_move("Bought") == "sell"
    assert next_move("Partially sold") == "sell"
    assert next_move("Completed") is None
    assert next_move("Supplies") is None


def test_highlights_narrow_to_the_side_being_offered() -> None:
    candidates = [(1, _REVENANT, "Pending buy"), (2, _REVENANT, "Bought")]

    assert live_price_highlights([(_REVENANT, "buy")], candidates) == {1: frozenset({"buy"})}
    assert live_price_highlights([(_REVENANT, "sell")], candidates) == {2: frozenset({"sell"})}


def test_an_unsided_box_gives_each_row_whatever_it_still_owes() -> None:
    candidates = [(1, _REVENANT, "Pending buy"), (2, _REVENANT, "Bought")]

    assert live_price_highlights([(_REVENANT, None)], candidates) == {
        1: frozenset({"buy"}),
        2: frozenset({"sell"}),
    }


def test_both_sides_at_once_mark_both_prices() -> None:
    """One item bought on one slot and sold on another is a flip in both directions."""
    candidates = [(1, _REVENANT, "Partially sold")]

    assert live_price_highlights([(_REVENANT, "buy"), (_REVENANT, "sell")], candidates) == {
        1: frozenset({"buy", "sell"})
    }


def test_a_row_with_no_side_left_still_takes_the_offer_rather_than_nothing() -> None:
    """A Supplies row is a likelier answer for "which row is this?" than no row at all."""
    candidates = [(1, _REVENANT, "Supplies")]

    assert live_price_highlights([(_REVENANT, "buy")], candidates) == {1: frozenset({"buy"})}


def test_an_item_with_no_journal_row_marks_nothing() -> None:
    assert live_price_highlights([(_WHIP, "buy")], [(1, _REVENANT, "Pending buy")]) == {}


def test_an_empty_slot_is_not_an_offer() -> None:
    assert live_price_highlights([(0, "buy")], [(1, _REVENANT, "Pending buy")]) == {}
