"""Drives the real MainWindow's attention blink: the yellow pulse that says "this is the
one you just finished buying".

The blink only plays for somebody who is looking, so most of these have to put the window
on screen and give it focus first — the offscreen platform supports both. What is queued
while nobody is looking is asserted through the pending sets and the sidebar dot.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import _PLANS_TAB_TITLE, MainWindow
from osrs_toolkit.models import MarketPoint
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter

_JOURNAL_PAGE = MainWindow.NAV_ITEMS.index("Trade Journal")


def _watching(window: MainWindow, qt_app: QApplication) -> None:
    """Put the player in front of the Trade Journal, which is what lets a blink play."""
    window.show()
    window.activateWindow()
    window.nav.setCurrentRow(_JOURNAL_PAGE)
    qt_app.processEvents()


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


def _write_slots(root: Path, slots: dict[str, object]) -> None:
    (root / "state" / "abc123.json").write_text(json.dumps(slots), encoding="utf-8")


def _offer(slot: int, state: str, item_id: int = 4_151) -> dict[str, object]:
    return {
        "slot": slot,
        "itemId": item_id,
        "itemName": "Abyssal whip",
        "offerPrice": 1_500,
        "totalQuantity": 10,
        "quantityFilled": 10 if state == "BOUGHT" else 4,
        "spentGp": 6_000,
        "state": state,
    }


def _lit_rows(window: MainWindow) -> set[str]:
    """The item names whose row is washed yellow at this instant."""
    table = window.journal_table
    return {
        table.item(row, 2).text()
        for row in range(table.rowCount())
        if table.item(row, 0).background().color().name() == window._flash_row_color
    }


def _finish_the_blink(window: MainWindow, qt_app: QApplication) -> None:
    """Run the flasher's beats to the end without waiting out the real timer."""
    for _beat in range(window._journal_flasher.BEATS):
        window._journal_flasher._beat()
        window._slot_flasher._beat()
    qt_app.processEvents()


# --- a finished buy ---------------------------------------------------------------------


def test_a_finished_buy_lights_up_its_journal_row(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The feature: the buy is done, and the row you now have to sell blinks yellow."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    _watching(window, qt_app)

    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    assert window._journal_flasher.is_lit(position_id)
    assert _lit_rows(window) == {"Abyssal whip"}


def test_the_blink_stops_by_itself_and_gives_the_row_back(
    window: MainWindow, qt_app: QApplication
) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    _watching(window, qt_app)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    _finish_the_blink(window, qt_app)

    assert window._journal_flasher.is_lit(position_id) is False
    assert _lit_rows(window) == set()


def test_a_re_render_mid_blink_keeps_the_row_lit(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The journal re-renders on every import that touches a position, which builds fresh
    cells with nothing painted on them."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    _watching(window, qt_app)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    window._render_journal()

    assert _lit_rows(window) == {"Abyssal whip"}


def test_start_up_does_not_blink_at_everything_already_bought(
    window: MainWindow, qt_app: QApplication
) -> None:
    """Positions left "Bought" overnight are where they were, not something that happened.

    ``_journal_statuses`` is put back to None to reproduce the first render of a session
    exactly: the real one happens inside ``__init__``, before a test can put anything in
    the database for it to find.
    """
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._journal_statuses = None
    _watching(window, qt_app)

    window._render_journal()

    assert window._journal_flasher.is_lit(position_id) is False
    assert _lit_rows(window) == set()


def test_an_untracked_buy_that_fills_in_one_go_still_blinks(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The other side of the same rule: a position that arrives already "Bought" during a
    session is news, because the offer behind it just finished."""
    window._render_journal()
    _watching(window, qt_app)

    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    assert _lit_rows(window) == {"Abyssal whip"}


def test_a_manual_entry_never_borrows_a_position_s_blink(
    window: MainWindow, qt_app: QApplication
) -> None:
    """Manual trade ids and position ids are numbered from different tables, so the two
    can collide; only tracked rows carry a flash key."""
    trade_id = window._journal.add("Rune platebody", 5, 38_000, 39_500)
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    assert trade_id == position_id, "the collision this guards against did not occur"
    window._render_journal()
    _watching(window, qt_app)

    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    assert _lit_rows(window) == {"Abyssal whip"}


# --- holding the blink until somebody is looking -----------------------------------------


def test_a_blink_waits_while_the_player_is_in_game(window: MainWindow) -> None:
    """The window is not even shown: a two-second pulse now would be spent on nobody."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()

    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    assert window._pending_journal_flash == {position_id}
    assert window._journal_flasher.is_lit(position_id) is False


def test_the_sidebar_says_something_is_waiting_and_stops_once_it_is_seen(
    window: MainWindow, qt_app: QApplication
) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    nav_item = window.nav.item(_JOURNAL_PAGE)
    assert nav_item.text() == "Trade Journal  ●"
    assert window.journal_tabs.tabText(0).endswith("●")

    _watching(window, qt_app)

    assert nav_item.text() == "Trade Journal"
    assert window.journal_tabs.tabText(0) == _PLANS_TAB_TITLE
    assert window._pending_journal_flash == set()
    assert window._journal_flasher.is_lit(position_id)


def test_a_blink_waits_out_a_different_journal_tab(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The slot cards sit above the tabs and blink anyway; the table does not."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    _watching(window, qt_app)
    window.journal_tabs.setCurrentIndex(1)

    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()
    assert window._pending_journal_flash == {position_id}

    window.journal_tabs.setCurrentIndex(0)

    assert window._pending_journal_flash == set()
    assert window._journal_flasher.is_lit(position_id)


# --- the Grand Exchange slots -------------------------------------------------------------


def test_an_offer_that_just_finished_lights_up_its_slot(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    window._render_ge_offers()
    _watching(window, qt_app)

    _write_slots(root, {"3": _offer(3, "BOUGHT")})
    window._render_ge_offers()

    assert window.ge_slot_cards[3].property("flash") == "on"
    assert window.ge_slot_cards[0].property("flash") == ""


def test_the_slot_goes_back_to_its_collect_colours_afterwards(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    window._render_ge_offers()
    _watching(window, qt_app)
    _write_slots(root, {"3": _offer(3, "BOUGHT")})
    window._render_ge_offers()

    _finish_the_blink(window, qt_app)

    card = window.ge_slot_cards[3]
    assert card.property("flash") == ""
    assert card.property("slotState") == "collect"


def test_re_reading_the_same_finished_offer_does_not_blink_again(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """An uncollected offer sits there, and the slots are re-read every three seconds."""
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    window._render_ge_offers()
    _watching(window, qt_app)
    _write_slots(root, {"3": _offer(3, "BOUGHT")})
    window._render_ge_offers()
    _finish_the_blink(window, qt_app)

    window._render_ge_offers()

    assert window.ge_slot_cards[3].property("flash") == ""
    assert window._slot_flasher.is_lit(3) is False


def test_the_slots_reseed_when_the_character_disconnects(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """Whatever is on the slots when a character next connects is where they already were."""
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    window._render_ge_offers()
    _watching(window, qt_app)

    window._sync_importer = RuneLiteSyncImporter(tmp_path / "gone")
    window._render_ge_offers()
    assert window._ge_slot_states is None

    window._sync_importer = RuneLiteSyncImporter(root)
    _write_slots(root, {"3": _offer(3, "BOUGHT")})
    window._render_ge_offers()

    assert window._slot_flasher.is_lit(3) is False


# --- finding a slot's row from the other direction -----------------------------------------


def test_clicking_a_slot_finds_and_lights_its_journal_row(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.track(1_234, "Dragon bones", 100, 2_000, 2_500)
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    window._render_journal()
    window._render_ge_offers()
    _watching(window, qt_app)

    window.ge_slot_cards[3].clicked.emit(4_151)

    assert window._journal_flasher.is_lit(position_id)
    assert _lit_rows(window) == {"Abyssal whip"}
    selected = window.journal_table.currentRow()
    assert window.journal_table.item(selected, 2).text() == "Abyssal whip"


def test_clicking_a_slot_widens_a_filter_that_was_hiding_the_row(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING")})
    _watching(window, qt_app)
    window.journal_status_filter.setCurrentText("Completed")
    assert window.journal_table.rowCount() == 0

    window.ge_slot_cards[3].clicked.emit(4_151)

    assert window.journal_status_filter.currentText() == "All statuses"
    assert window._journal_flasher.is_lit(position_id)


def test_clicking_a_slot_widens_a_period_that_was_hiding_the_row(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """A finished flip whose coins are still in the slot can fall outside the period
    window as easily as outside the status one."""
    position_id = window._journal.track(1_515, "Yew logs", 5_000, 320, 341)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(5_000, 341)], [(5_000, 320)]
    )
    root = _connect(window, tmp_path)
    _write_slots(root, {"5": _offer(5, "SOLD", item_id=1_515)})
    _watching(window, qt_app)
    window.journal_period_filter.setCurrentText("This year")
    # Backdate the completion out of every window but "All time".
    with window._journal._connect() as connection:
        connection.execute(
            "UPDATE tracked_trades SET completed_at = ? WHERE position_id = ?",
            ("2019-04-01T12:00:00+00:00", position_id),
        )
    window._render_journal()
    assert window.journal_table.rowCount() == 0

    window.ge_slot_cards[5].clicked.emit(1_515)

    assert window.journal_period_filter.currentText() == "All time"
    assert window._journal_flasher.is_lit(position_id)


def test_clicking_a_slot_with_no_journal_row_says_so(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BUYING", item_id=9_999)})
    window._render_ge_offers()
    _watching(window, qt_app)

    window.ge_slot_cards[3].clicked.emit(9_999)

    assert window.ge_slot_hint.isHidden() is False
    assert "no journal row yet" in window.ge_slot_hint.text()


def test_an_empty_slot_is_not_clickable(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    _connect(window, tmp_path)
    window._render_ge_offers()

    assert window.ge_slot_cards[0]._item_id == 0


# --- the whole chain, from a real plugin event ------------------------------------------------


def test_a_real_fill_event_lights_the_row_and_the_slot_together(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """End to end, the way it happens in play: the plugin writes the fill that finishes a
    buy and leaves the offer sitting in its slot, and both places say so at once."""
    root = _connect(window, tmp_path)
    _write_slots(root, {"2": _offer(2, "BUYING")})
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()
    window._render_ge_offers()
    _watching(window, qt_app)

    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "events" / "fill.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": "9ca987d2-bcd1-45ea-8320-b17a956e38c9",
                "event_type": "ge_fill",
                "occurred_at": "2026-08-19T05:00:00Z",
                "account": {"hash": "abc123", "name": "Example Player"},
                "payload": {
                    "offer_id": "offer-1",
                    "offer_slot": 2,
                    "offer_price": 1_500,
                    "offer_state": "BOUGHT",
                    "side": "buy",
                    "item_id": 4_151,
                    "item_name": "Abyssal whip",
                    "quantity": 10,
                    "coins": 15_000,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_slots(root, {"2": _offer(2, "BOUGHT")})

    result = window._sync_importer.import_pending(window._journal, {})
    assert result.applied_to_tracked == 1
    window._render_journal()
    window._render_ge_offers()

    assert window._journal.list_tracked()[0].status == "Bought"
    assert _lit_rows(window) == {"Abyssal whip"}
    assert window._journal_flasher.is_lit(position_id)
    assert window.ge_slot_cards[2].property("flash") == "on"


# --- tracking a flip points at the row it just made ------------------------------------------


def test_tracking_a_flip_lights_the_row_it_created(
    window: MainWindow, qt_app: QApplication
) -> None:
    """Tracking throws the player onto a page they were not looking at, at a table that may
    already hold fifty rows."""
    from osrs_toolkit.models import FlipCandidate

    window.show()
    window.activateWindow()
    qt_app.processEvents()
    candidate = FlipCandidate(
        item_id=4_151,
        name="Abyssal whip",
        buy_price=1_500,
        sell_price=1_700,
        tax=34,
        profit_each=166,
        roi=11.1,
        hourly_volume=400,
        projected_volume=1_600,
        buy_limit=70,
        suggested_quantity=10,
        capital_required=15_000,
        potential_profit=1_660,
        confidence=67,
        age_seconds=120,
        score=8.0,
    )

    window._track_candidate(candidate)

    assert window.pages.currentIndex() == _JOURNAL_PAGE
    assert _lit_rows(window) == {"Abyssal whip"}


def test_clicking_the_next_slot_drops_the_last_slot_s_row(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """Regression: the blink joined whatever was already blinking, which is right for two
    offers finishing at once and wrong for a player clicking along their slots. Every row
    asked about stayed lit, so the second click blinked the first click's row beside it and
    working down all eight washed the whole table amber."""
    whip = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    bones = window._journal.track(1_234, "Dragon bones", 100, 2_000, 2_500)
    root = _connect(window, tmp_path)
    _write_slots(
        root,
        {
            "3": _offer(3, "BUYING"),
            "4": _offer(4, "BUYING", item_id=1_234),
        },
    )
    window._render_journal()
    window._render_ge_offers()
    _watching(window, qt_app)

    window.ge_slot_cards[3].clicked.emit(4_151)
    window.ge_slot_cards[4].clicked.emit(1_234)

    assert window._journal_flasher.is_lit(bones)
    assert window._journal_flasher.is_lit(whip) is False
    assert _lit_rows(window) == {"Dragon bones"}


def test_two_offers_finishing_together_still_share_one_blink(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The behaviour ``focus`` must not have taken away: these are two things that both
    happened, and the second must not cut the first one's blink short."""
    whip = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    bones = window._journal.track(1_234, "Dragon bones", 100, 2_000, 2_500)
    window._render_journal()
    _watching(window, qt_app)

    window._journal.update_tracked(whip, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()
    window._journal.update_tracked(bones, "Bought", None, None, None, [(100, 2_000)])
    window._render_journal()

    assert _lit_rows(window) == {"Abyssal whip", "Dragon bones"}


# --- ready to sell ----------------------------------------------------------------------


def _ask_cell(window: MainWindow, item_name: str):
    table = window.journal_table
    row = next(
        row for row in range(table.rowCount()) if table.item(row, 2).text() == item_name
    )
    return table.item(row, 6)


def test_a_collected_buy_picks_out_the_price_to_ask(window: MainWindow) -> None:
    """The blink is over in two seconds and the player is still in game. What they come back
    for is the number to type into the sell offer, so it stays picked out until it is."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.text() == "1,700 gp"
    assert ask.font().bold()
    assert ask.foreground().color().name() == window._warning_color
    assert "price to ask" in ask.toolTip()


def test_a_position_still_buying_does_not_shout_a_price_at_anyone(
    window: MainWindow,
) -> None:
    window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.font().bold() is False
    assert ask.foreground().color().name() != window._warning_color


def test_the_price_stops_shouting_once_the_offer_is_listed(window: MainWindow) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()
    assert _ask_cell(window, "Abyssal whip").font().bold()

    window._journal.apply_offer_opened(
        item_id=4_151, item_name="Abyssal whip", side="sell", total_quantity=10,
        offer_price=1_700,
    )
    window._render_journal()

    assert _ask_cell(window, "Abyssal whip").font().bold() is False


def _live_point(item_id: int, *, sell_target: int) -> MarketPoint:
    """A market snapshot whose passive sell target is ``sell_target``.

    Matches ``test_journal_needs_attention.py``'s helper: ``offer_targets`` takes the sell
    side from ``point.high``, not ``point.low`` — ``low`` only feeds the buy side.
    """
    return MarketPoint(
        item_id=item_id,
        high=sell_target,
        low=sell_target - 200,
        high_time=1_700_000_000,
        low_time=1_700_000_000,
        volume_5m=1_000,
        volume_1h=10_000,
    )


def test_the_price_to_ask_prefers_the_live_market_over_a_frozen_plan(
    window: MainWindow,
) -> None:
    """The bug this guards: the flip was planned at 1,700 hours before the buy filled, the
    market has since dropped to 1,550 — and the frozen plan was what got highlighted,
    confidently, as the number to type into the game."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._points = [_live_point(4_151, sell_target=1_550)]
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.text() == "1,550 gp"
    assert ask.foreground().color().name() == window._warning_color


def test_a_price_that_does_not_clear_cost_is_never_dressed_up_as_sound(
    window: MainWindow,
) -> None:
    """The concrete harm reported: a position with nothing better than its own buy price
    to fall back on got that price highlighted in the same confident amber as a real
    suggestion, and it read as advice to list at a guaranteed loss."""
    # Mirrors what an auto-created position looks like: nothing beat the buy price at the
    # moment it was tracked, so the sell target mirrors it exactly.
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_500)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.text() == "1,500 gp"
    assert ask.foreground().color().name() == window._loss_color
    assert "sell at a loss" in ask.toolTip()


def test_a_live_price_below_cost_is_also_treated_as_unsound(window: MainWindow) -> None:
    """Live data is preferred for being fresher, not for being flattering — a market that
    has genuinely dropped below cost must still warn rather than reassure."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._points = [_live_point(4_151, sell_target=1_480)]
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.text() == "1,480 gp"
    assert ask.foreground().color().name() == window._loss_color


def test_a_price_that_clears_cost_only_barely_still_reads_as_sound(
    window: MainWindow,
) -> None:
    """The GE tax has to actually be cleared, not just the raw buy price — but a number
    that does clear it, even narrowly, is real advice and should read as such."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_540)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()

    ask = _ask_cell(window, "Abyssal whip")
    assert ask.foreground().color().name() == window._warning_color


# --- a flip that finishes behind the Active trades filter --------------------------------


def test_completing_a_flip_under_active_trades_keeps_the_dot_lit(
    window: MainWindow, qt_app: QApplication
) -> None:
    """The bug this guards: Active trades is the default filter, and a sale finishing is
    exactly the transition that drops a row out of it — the same instant the flip closes
    out. Delivering the flash there marked it seen against a row nobody was ever shown,
    so the dot went dark and the only trace of the completion vanished with it."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    assert window.journal_status_filter.currentText() == "All statuses"
    window.journal_status_filter.setCurrentText("Active trades")
    _watching(window, qt_app)

    window._render_journal()

    assert _lit_rows(window) == set()
    assert window.nav.item(_JOURNAL_PAGE).text() == "Trade Journal  ●"
    assert window._pending_journal_flash == {position_id}


def test_widening_the_filter_delivers_the_flash_it_was_waiting_on(
    window: MainWindow, qt_app: QApplication
) -> None:
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    window.journal_status_filter.setCurrentText("Active trades")
    _watching(window, qt_app)
    window._render_journal()
    assert window._pending_journal_flash == {position_id}

    window.journal_status_filter.setCurrentText("All statuses")

    assert _lit_rows(window) == {"Abyssal whip"}
    assert window._pending_journal_flash == set()
    assert window.nav.item(_JOURNAL_PAGE).text() == "Trade Journal"


def test_a_second_flip_finishing_still_shows_while_the_first_stays_queued(
    window: MainWindow, qt_app: QApplication
) -> None:
    """One position hidden by the filter must not swallow the flash of another the
    filter is showing just fine."""
    hidden = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        hidden, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    shown = window._journal.track(1_234, "Dragon bones", 100, 2_000, 2_500)
    window.journal_status_filter.setCurrentText("Active trades")
    _watching(window, qt_app)
    window._render_journal()
    assert window._pending_journal_flash == {hidden}

    window._journal.update_tracked(shown, "Bought", None, None, None, [(100, 2_000)])
    window._render_journal()

    assert _lit_rows(window) == {"Dragon bones"}
    assert window._pending_journal_flash == {hidden}
    assert window.nav.item(_JOURNAL_PAGE).text() == "Trade Journal  ●"


# --- collecting answers a queued "Completed" flash before anyone sees it ----------------


def test_collecting_a_finished_sale_cancels_the_flash_nobody_saw_yet(
    window: MainWindow, tmp_path: Path
) -> None:
    """The scenario reported live: a sale finishes while the player is in-game, they
    collect the coins straight off the Grand Exchange interface, and only afterwards open
    the app. The flash was still queued behind the default "Active trades" filter — the
    exact case ``_release_pending_flashes`` holds it for — but collecting already answered
    the only thing it was going to say."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    window._render_journal()
    assert window._pending_journal_flash == {position_id}

    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "SOLD")})
    window._render_ge_offers()
    assert window._pending_journal_flash == {position_id}

    _write_slots(root, {})  # the plugin drops a slot the instant it is collected
    window._render_ge_offers()

    assert window._pending_journal_flash == set()


def test_the_sidebar_dot_goes_dark_the_moment_it_is_collected(
    window: MainWindow, tmp_path: Path
) -> None:
    """The dot exists to say something is waiting; collecting is exactly what stops it
    being true, so it should not linger a render behind the pending set clearing."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    window._render_journal()
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "SOLD")})
    window._render_ge_offers()
    assert window.nav.item(_JOURNAL_PAGE).text() == "Trade Journal  ●"

    _write_slots(root, {})
    window._render_ge_offers()

    assert window.nav.item(_JOURNAL_PAGE).text() == "Trade Journal"


def test_a_cancelled_flash_never_plays_even_if_the_filter_widens_later(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """Once collecting has answered it, widening the filter afterwards must not resurrect
    it — the row already reads "Completed"; lighting it up now says nothing new."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    window.journal_status_filter.setCurrentText("Active trades")
    window._render_journal()
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "SOLD")})
    window._render_ge_offers()
    _write_slots(root, {})
    window._render_ge_offers()
    assert window._pending_journal_flash == set()

    _watching(window, qt_app)
    window.journal_status_filter.setCurrentText("All statuses")

    assert _lit_rows(window) == set()
    assert window._journal_flasher.is_lit(position_id) is False


def test_collecting_a_bought_item_does_not_cancel_the_go_list_flash(
    window: MainWindow, tmp_path: Path
) -> None:
    """A "Bought" flash means something different from a "Completed" one — go list this —
    and collecting the goods from a finished buy is what makes listing possible, not what
    answers it, so this reason must survive the same collection event that cancels the
    other one."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(position_id, "Bought", None, None, None, [(10, 1_500)])
    window._render_journal()
    assert window._pending_journal_flash == {position_id}

    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "BOUGHT")})
    window._render_ge_offers()
    _write_slots(root, {})
    window._render_ge_offers()

    assert window._pending_journal_flash == {position_id}


def test_collecting_an_unrelated_item_leaves_the_queue_alone(
    window: MainWindow, tmp_path: Path
) -> None:
    """The cancellation is keyed to the specific item collected, not "something on the
    Grand Exchange changed" — a different position's flash must not be swept up with it."""
    position_id = window._journal.track(4_151, "Abyssal whip", 10, 1_500, 1_700)
    window._journal.update_tracked(
        position_id, "Completed", 1_500, 1_700, [(10, 1_700)], [(10, 1_500)]
    )
    window._render_journal()
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3, "SOLD", item_id=999)})
    window._render_ge_offers()

    _write_slots(root, {})
    window._render_ge_offers()

    assert window._pending_journal_flash == {position_id}
