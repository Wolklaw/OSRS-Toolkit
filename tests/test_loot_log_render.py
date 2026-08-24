"""Drives the real MainWindow._render_loot_log, the way test_buy_limits_render.py drives
_render_buy_limits — the widgets it fills only exist once the whole page is built.
"""

from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal import LoadoutItem, NpcLootRecord


def _loot(event_id: str, npc_name: str = "Vorkath") -> NpcLootRecord:
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
        account_hash="hash",
        account_name="Player",
        npc_name=npc_name,
        items=(
            LoadoutItem(item_id=995, item_name="Coins", quantity=10_000, unit_value=1),
        ),
    )


def test_empty_state_with_nothing_looted(window: MainWindow) -> None:
    window._render_loot_log()

    assert window.loot_log_table.rowCount() == 0


def test_a_loot_delivery_shows_up(window: MainWindow) -> None:
    window._journal.add_npc_loot_event(_loot("evt-1"))

    window._render_loot_log()

    assert window.loot_log_table.rowCount() == 1
    assert window.loot_log_table.item(0, 1).text() == "Vorkath"
    assert window.loot_log_table.item(0, 2).text() == "Player"


def test_deleting_the_selected_loot_entry_removes_it(window: MainWindow) -> None:
    window._journal.add_npc_loot_event(_loot("evt-1"))
    window._render_loot_log()
    window.loot_log_table.selectRow(0)

    window._journal.delete_npc_loot_event("evt-1")
    window._render_loot_log()

    assert window.loot_log_table.rowCount() == 0


def test_selecting_a_row_enables_the_delete_button(window: MainWindow) -> None:
    window._journal.add_npc_loot_event(_loot("evt-1"))
    window._render_loot_log()

    assert window._loot_log_row_buttons[0].isEnabled() is False

    window.loot_log_table.selectRow(0)

    assert window._loot_log_row_buttons[0].isEnabled() is True
