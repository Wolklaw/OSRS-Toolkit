"""Drives the real MainWindow's answer to "what am I supposed to type in here?".

The RuneLite plugin reports where in the Grand Exchange the player is standing, and the journal
picks out the rows that are about — the whole row washed, and the two figures the trade needs
picked out inside it. It follows a trade the whole way: the "Set up offer" box narrows to one
item, and the screens either side of it, where the interface names no item at all, fall back to
whatever is out on the slots. These tests drive it from the plugin's own files outwards, the way
``test_ge_offers_render.py`` does, so what is asserted is what the table would really show.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal_presentation import offer_screen_positions
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter

_REVENANT = 21_802


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


def _place_offer(
    root: Path, item_id: int = _REVENANT, state: str = "BUYING", slot: int = 0
) -> None:
    (root / "state" / "abc123.json").write_text(
        json.dumps(
            {
                str(slot): {
                    "slot": slot,
                    "itemId": item_id,
                    "itemName": "Revenant cave teleport",
                    "offerPrice": 606,
                    "totalQuantity": 7_788,
                    "quantityFilled": 100,
                    "spentGp": 60_600,
                    "state": state,
                }
            }
        ),
        encoding="utf-8",
    )


def _collect_everything(root: Path) -> None:
    (root / "state" / "abc123.json").write_text(json.dumps({}), encoding="utf-8")


def _washed_rows(window: MainWindow) -> set[str]:
    """The item names whose row is washed in the "open in game" colour right now."""
    table = window.journal_table
    return {
        table.item(row, 2).text()
        for row in range(table.rowCount())
        if table.item(row, 0).background().color().name() == window._live_offer_row_color
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


# --- the feature ------------------------------------------------------------------------


def test_the_open_offer_box_picks_out_the_row_and_its_two_figures(
    window: MainWindow, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)

    window._refresh_offer_screen()

    assert _washed_rows(window) == {"Revenant cave teleport"}
    # Quantity and Buy suggestion: the two boxes the game is waiting on. The Status cell is
    # bold on every row already, so it comes along and is not what this is about.
    assert {3, 4} <= _bold_columns(window, "Revenant cave teleport")
    assert window.journal_table.item(0, 3).foreground().color().name() == (window._live_offer_color)


def test_closing_the_box_takes_the_highlight_back(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)
    window._refresh_offer_screen()

    _walk_away(root)
    window._refresh_offer_screen()

    assert _washed_rows(window) == set()
    assert 3 not in _bold_columns(window, "Revenant cave teleport")


def test_a_box_the_plugin_stopped_stamping_stops_being_believed(
    window: MainWindow, tmp_path: Path
) -> None:
    """A client killed with the interface up deletes nothing on its way out."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root, age=timedelta(minutes=10))

    window._refresh_offer_screen()

    assert _washed_rows(window) == set()


def test_selling_points_at_the_sell_price_not_the_buy_one(
    window: MainWindow, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    position_id = window._journal.track(_REVENANT, "Revenant cave teleport", 500, 642, 672)
    window._journal.update_tracked(position_id, "Listed for sale", None, None, None, [])
    _open_offer_box(root, side="sell")

    window._refresh_offer_screen()

    bold = _bold_columns(window, "Revenant cave teleport")
    assert 6 in bold
    assert 4 not in bold


def test_both_rows_for_one_item_light_when_both_are_waiting_to_buy(
    window: MainWindow, tmp_path: Path
) -> None:
    """Two pending buys for the same item is ordinary. Picking one would be a guess, and a
    wrong guess points at the wrong quantity — the number this exists to give."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(_REVENANT, "Revenant cave teleport", 4_807, 624, 673)
    _open_offer_box(root)

    window._refresh_offer_screen()

    table = window.journal_table
    washed = [
        table.item(row, 3).text()
        for row in range(table.rowCount())
        if table.item(row, 0).background().color().name() == window._live_offer_row_color
    ]
    assert sorted(washed) == ["4,807", "7,788"]


def test_a_row_for_a_different_item_is_left_alone(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(4_151, "Abyssal whip", 1, 1_500, 1_700)
    _open_offer_box(root)

    window._refresh_offer_screen()

    assert _washed_rows(window) == {"Revenant cave teleport"}


def test_an_item_with_no_journal_row_highlights_nothing(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(4_151, "Abyssal whip", 1, 1_500, 1_700)
    _open_offer_box(root)

    window._refresh_offer_screen()

    assert _washed_rows(window) == set()


def test_the_blink_wins_the_row_while_it_is_lit_and_hands_it_back_after(
    window: MainWindow, tmp_path: Path
) -> None:
    """Both washes want the same background. The blink is the louder and shorter of the two."""
    root = _connect(window, tmp_path)
    position_id = window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)
    window._refresh_offer_screen()
    window.show()
    window.activateWindow()
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index("Trade Journal"))

    window._journal_flasher.start({position_id})
    assert _washed_rows(window) == set()

    for _beat in range(window._journal_flasher.BEATS):
        window._journal_flasher._beat()

    assert _washed_rows(window) == {"Revenant cave teleport"}


# --- which rows an open box is about ------------------------------------------------------


def test_offer_screen_positions_narrows_to_the_side_being_offered() -> None:
    candidates = [(1, _REVENANT, "Pending buy"), (2, _REVENANT, "Bought")]

    assert offer_screen_positions(_REVENANT, "buy", candidates) == frozenset({1})
    assert offer_screen_positions(_REVENANT, "sell", candidates) == frozenset({2})
    assert offer_screen_positions(_REVENANT, None, candidates) == frozenset({1, 2})


def test_offer_screen_positions_gives_way_rather_than_answering_nothing() -> None:
    """A row in a status with no side at all is still likelier to be the one wanted."""
    candidates = [(1, _REVENANT, "Supplies")]

    assert offer_screen_positions(_REVENANT, "buy", candidates) == frozenset({1})


def test_offer_screen_positions_has_nothing_to_say_without_an_open_box() -> None:
    assert offer_screen_positions(None, "buy", [(1, _REVENANT, "Pending buy")]) == frozenset()


# --- the rest of the trade ----------------------------------------------------------------


def test_the_row_stays_lit_after_the_offer_is_confirmed(window: MainWindow, tmp_path: Path) -> None:
    """Confirming closes the box but not the trade: the offer is now filling in front of you."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _open_offer_box(root)
    window._refresh_offer_screen()

    _place_offer(root)
    _stand_at_the_grand_exchange(root)
    window._refresh_offer_screen()

    assert _washed_rows(window) == {"Revenant cave teleport"}
    assert {3, 4} <= _bold_columns(window, "Revenant cave teleport")


def test_collecting_the_last_offer_puts_the_highlight_out(
    window: MainWindow, tmp_path: Path
) -> None:
    """Collecting empties a slot without producing an event, so the poll has to notice it."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _place_offer(root, state="BOUGHT")
    _stand_at_the_grand_exchange(root)
    window._refresh_offer_screen()
    assert _washed_rows(window) == {"Revenant cave teleport"}

    _collect_everything(root)
    window._refresh_offer_screen()

    assert _washed_rows(window) == set()


def test_a_live_sell_offer_points_at_the_sell_price(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    position_id = window._journal.track(_REVENANT, "Revenant cave teleport", 500, 642, 672)
    window._journal.update_tracked(position_id, "Listed for sale", None, None, None, [])
    _place_offer(root, state="SELLING")
    _stand_at_the_grand_exchange(root)

    window._refresh_offer_screen()

    bold = _bold_columns(window, "Revenant cave teleport")
    assert 6 in bold
    assert 4 not in bold


def test_standing_at_the_slots_leaves_rows_with_no_offer_alone(
    window: MainWindow, tmp_path: Path
) -> None:
    """A plan you have not placed yet is not something you are looking at."""
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    window._journal.track(4_151, "Abyssal whip", 1, 1_500, 1_700)
    _place_offer(root)
    _stand_at_the_grand_exchange(root)

    window._refresh_offer_screen()

    assert _washed_rows(window) == {"Revenant cave teleport"}


def test_opening_a_box_narrows_from_every_live_offer_to_the_one_item(
    window: MainWindow, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    whip = window._journal.track(4_151, "Abyssal whip", 1, 1_500, 1_700)
    window._journal.update_tracked(whip, "Listed for sale", None, None, None, [])
    (root / "state" / "abc123.json").write_text(
        json.dumps(
            {
                "0": {
                    "slot": 0,
                    "itemId": _REVENANT,
                    "itemName": "Revenant cave teleport",
                    "offerPrice": 606,
                    "totalQuantity": 7_788,
                    "quantityFilled": 100,
                    "spentGp": 60_600,
                    "state": "BUYING",
                },
                "1": {
                    "slot": 1,
                    "itemId": 4_151,
                    "itemName": "Abyssal whip",
                    "offerPrice": 1_700,
                    "totalQuantity": 1,
                    "quantityFilled": 0,
                    "spentGp": 0,
                    "state": "SELLING",
                },
            }
        ),
        encoding="utf-8",
    )
    _stand_at_the_grand_exchange(root)
    window._refresh_offer_screen()
    assert _washed_rows(window) == {"Revenant cave teleport", "Abyssal whip"}

    _open_offer_box(root)
    window._refresh_offer_screen()

    assert _washed_rows(window) == {"Revenant cave teleport"}


def test_walking_away_from_the_grand_exchange_puts_everything_out(
    window: MainWindow, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    window._journal.track(_REVENANT, "Revenant cave teleport", 7_788, 642, 672)
    _place_offer(root)
    _stand_at_the_grand_exchange(root)
    window._refresh_offer_screen()

    _walk_away(root)
    window._refresh_offer_screen()

    assert _washed_rows(window) == set()
