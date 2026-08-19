"""Drives the real MainWindow._render_buy_limits, the way test_journal_summary.py drives
_render_journal — the widgets it fills only exist once the whole page is built.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal import SyncedItem, SyncedTrade
from osrs_toolkit.models import ItemMapping
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter


def _buy(item_id: int, item_name: str, quantity: int, ago: timedelta) -> SyncedTrade:
    occurred_at = (datetime.now(UTC) - ago).isoformat(timespec="seconds")
    return SyncedTrade(
        event_id=f"{item_id}-{ago}",
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(flow="received", item_id=item_id, item_name=item_name, quantity=quantity, unit_value=100),
            SyncedItem(flow="given", item_id=995, item_name="Coins", quantity=quantity * 100, unit_value=1),
        ),
    )


def _connect(window: MainWindow, tmp_path: Path) -> None:
    """Give the importer a sync root that exists, which is what "plugin installed" means."""
    root = tmp_path / "sync"
    root.mkdir()
    window._sync_importer = RuneLiteSyncImporter(root)


def test_empty_state_with_nothing_bought(window: MainWindow, tmp_path: Path) -> None:
    _connect(window, tmp_path)

    window._render_buy_limits()

    assert window.buy_limits_table.rowCount() == 0
    assert window.buy_limits_empty.isHidden() is False
    assert "Nothing is currently limited" in window.buy_limits_empty.text()


def test_an_empty_tab_without_the_plugin_does_not_read_as_room_to_buy(
    window: MainWindow,
) -> None:
    """Nothing counts purchases without the plugin, so "nothing is limited" would be a
    claim about the account rather than about what this tab can see."""
    window._render_buy_limits()

    assert window.buy_limits_empty.isHidden() is False
    text = window.buy_limits_empty.text()
    assert "not connected" in text
    assert "not saying you have room left" in text


def test_a_recent_buy_with_a_known_limit_shows_up(window: MainWindow) -> None:
    window._mappings = {4_151: ItemMapping(4_151, "Whip", False, 8, None)}
    window._journal.add_synced_trades([_buy(4_151, "Whip", 5, timedelta(hours=1))])

    window._render_buy_limits()

    assert window.buy_limits_table.rowCount() == 1
    assert window.buy_limits_table.item(0, 0).text() == "Whip"
    assert window.buy_limits_table.item(0, 1).text() == "5"
    assert window.buy_limits_table.item(0, 2).text() == "8"
    assert window.buy_limits_table.item(0, 3).text() == "3"
    assert window.buy_limits_empty.isHidden() is True


def test_a_buy_older_than_four_hours_is_excluded(window: MainWindow) -> None:
    window._mappings = {4_151: ItemMapping(4_151, "Whip", False, 8, None)}
    window._journal.add_synced_trades([_buy(4_151, "Whip", 5, timedelta(hours=5))])

    window._render_buy_limits()

    assert window.buy_limits_table.rowCount() == 0


def test_a_buy_with_no_known_limit_is_excluded(window: MainWindow) -> None:
    window._journal.add_synced_trades([_buy(4_151, "Whip", 5, timedelta(hours=1))])

    window._render_buy_limits()

    assert window.buy_limits_table.rowCount() == 0
