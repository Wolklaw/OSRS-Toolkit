"""Drives the real MainWindow._render_death_log, the way test_buy_limits_render.py drives
_render_buy_limits — the widgets it fills only exist once the whole page is built.
"""

from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.app import MainWindow
from osrs_toolkit.journal import LoadoutItem, PlayerDeathRecord


def _death(event_id: str, skulled: bool = True) -> PlayerDeathRecord:
    return PlayerDeathRecord(
        event_id=event_id,
        occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
        account_hash="hash",
        account_name="Player",
        skulled=skulled,
        equipment=(LoadoutItem(item_id=4151, item_name="Whip", quantity=1, unit_value=1_500_000),),
        inventory=(),
    )


def test_empty_state_with_no_deaths(window: MainWindow) -> None:
    window._render_death_log()

    assert window.death_log_table.rowCount() == 0


def test_a_death_shows_up(window: MainWindow) -> None:
    window._journal.add_player_death_event(_death("evt-1"))

    window._render_death_log()

    assert window.death_log_table.rowCount() == 1
    assert window.death_log_table.item(0, 1).text() == "Player"
    assert window.death_log_table.item(0, 2).text() == "Skulled"
    assert "Whip" in window.death_log_table.item(0, 3).text()


def test_a_non_skulled_death_is_labeled_accordingly(window: MainWindow) -> None:
    window._journal.add_player_death_event(_death("evt-1", skulled=False))

    window._render_death_log()

    assert window.death_log_table.item(0, 2).text() == "Not skulled"


def test_deleting_the_selected_death_entry_removes_it(window: MainWindow) -> None:
    window._journal.add_player_death_event(_death("evt-1"))
    window._render_death_log()
    window.death_log_table.selectRow(0)

    window._journal.delete_player_death_event("evt-1")
    window._render_death_log()

    assert window.death_log_table.rowCount() == 0


def test_selecting_a_row_enables_the_delete_button(window: MainWindow) -> None:
    window._journal.add_player_death_event(_death("evt-1"))
    window._render_death_log()

    assert window._death_log_row_buttons[0].isEnabled() is False

    window.death_log_table.selectRow(0)

    assert window._death_log_row_buttons[0].isEnabled() is True
