"""Regression tests for row identity surviving a user-applied column sort.

``_fill_table`` re-enables sorting once it has populated a table, which makes Qt sort
immediately by whatever indicator the user last clicked. Anything that pairs a table row
with its source record must therefore key off the cell, not the visual row index.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt

from osrs_toolkit.app import MainWindow
from osrs_toolkit.calculators import SKILL_GUIDES, SKILL_METHODS
from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.pvm import PVM_ACTIVITIES


def _price_every_skilling_input(window: MainWindow) -> None:
    """Give every recipe item a fresh price so all skilling methods render."""
    now = int(time.time())
    item_ids = {
        item.item_id for method in SKILL_METHODS for item in (*method.inputs, *method.outputs)
    }
    window._mappings = {
        item_id: ItemMapping(item_id, f"Item {item_id}", False, 1_000, 100) for item_id in item_ids
    }
    window._points = [
        MarketPoint(
            item_id=item_id,
            high=200,
            low=150,
            high_time=now - 60,
            low_time=now - 60,
            volume_5m=500,
            volume_1h=5_000,
        )
        for item_id in item_ids
    ]


def test_skilling_guide_links_follow_their_row_after_a_user_sort(window: MainWindow) -> None:
    """Regression: sorting by Method then re-rendering must not shuffle guide URLs."""
    _price_every_skilling_input(window)
    window.skill_profitable.setChecked(False)
    window._render_skilling()
    table = window.skill_table
    assert table.rowCount() == len(SKILL_METHODS)

    # The user sorts by Method (A–Z) instead of the default GP/hr ordering.
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    # Any re-render — typing in the search box does this on every keystroke.
    window._render_skilling()

    mismatched = [
        (table.item(row, 0).text(), table.item(row, 1).text())
        for row in range(table.rowCount())
        if table.item(row, 11).data(Qt.ItemDataRole.UserRole)
        != SKILL_GUIDES[table.item(row, 1).text()]
    ]
    assert not mismatched, f"{len(mismatched)} rows link to another skill's guide: {mismatched[:3]}"


def test_pvm_readiness_colour_follows_its_row_after_a_user_sort(window: MainWindow) -> None:
    """Regression: a sorted PvM table must not paint 'Ready' rows with the loss colour."""
    window._render_pvm()
    table = window.pvm_table
    assert table.rowCount() >= 2

    # The user sorts by Activity name, breaking the ready-first source ordering.
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    window._render_pvm()

    wiki_urls = {activity.name: activity.wiki_url for activity in PVM_ACTIVITIES}
    colours = {
        "Ready": window._profit_color.casefold(),
        "Not ready": window._loss_color.casefold(),
        # No gear has been synced in this fixture, so nothing has been checked either way.
        "Unknown": window._muted_color.casefold(),
    }
    for row in range(table.rowCount()):
        status = table.item(row, 1).text()
        actual = table.item(row, 1).foreground().color().name().casefold()
        expected = colours[status]
        assert actual == expected, f"row {row} is {status!r} but painted {actual}"

        activity = table.item(row, 0).text()
        assert table.item(row, 6).data(Qt.ItemDataRole.UserRole) == wiki_urls[activity], (
            f"row {row} shows {activity!r} but links to another activity's wiki page"
        )


def test_selected_synced_trade_survives_a_user_sort(window: MainWindow) -> None:
    """Regression: deleting a sorted activity row must not delete a different trade."""
    from osrs_toolkit.journal import SyncedItem, SyncedTrade

    def fill(event_id: str, occurred_at: str, item_name: str) -> SyncedTrade:
        return SyncedTrade(
            event_id=event_id,
            occurred_at=occurred_at,
            event_type="ge_fill",
            account_hash="hash",
            account_name="Zed",
            counterparty=None,
            direction="buy",
            metadata={},
            items=(
                SyncedItem("given", 995, "Coins", 1_000, 1),
                SyncedItem("received", 1, item_name, 1, 1_000),
            ),
        )

    window._journal.add_synced_trades(
        [
            fill(
                "11111111-1111-4111-8111-111111111111", "2026-01-01T10:00:00+00:00", "Zulrah scale"
            ),
            fill(
                "22222222-2222-4222-8222-222222222222", "2026-01-02T10:00:00+00:00", "Abyssal whip"
            ),
        ]
    )
    window._render_synced_trades()
    table = window.synced_trade_table
    assert table.rowCount() == 2

    # Newest-first by default puts "Abyssal whip" (Jan 2) on row 0; sorting by Time
    # ascending swaps them.
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    table.setCurrentCell(0, 0)

    selected = window._selected_synced_trade()
    assert selected is not None
    trade, event_ids = selected
    # Row 0 now displays the older Jan 1 fill; the selection must agree with the screen.
    assert table.item(0, 3).text() == "Bought Zulrah scale"
    assert trade.received[0].item_name == "Zulrah scale"
    assert event_ids == ("11111111-1111-4111-8111-111111111111",)
