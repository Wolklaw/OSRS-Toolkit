"""``session_summary`` infers a session from the gaps between companion events -- loot,
deaths, and player trades -- rather than being told where one starts or ends.
"""

from __future__ import annotations

from pathlib import Path

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    LootByNpc,
    NpcLootRecord,
    PlayerDeathRecord,
    SyncedItem,
    SyncedTrade,
)

ACCOUNT_HASH = "123456789"
ACCOUNT_NAME = "Example Player"


def _repository(tmp_path: Path) -> JournalRepository:
    return JournalRepository(tmp_path / "journal.db")


def _loot(event_id: str, occurred_at: str, npc_name: str, value: int) -> NpcLootRecord:
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=occurred_at,
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        npc_name=npc_name,
        items=(LoadoutItem(item_id=995, item_name="Coins", quantity=value, unit_value=1),),
    )


def _death(event_id: str, occurred_at: str, value: int) -> PlayerDeathRecord:
    return PlayerDeathRecord(
        event_id=event_id,
        occurred_at=occurred_at,
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        skulled=False,
        equipment=(
            LoadoutItem(item_id=1, item_name="Rune scimitar", quantity=1, unit_value=value),
        ),
        inventory=(),
    )


def _trade(event_id: str, occurred_at: str, received_value: int) -> SyncedTrade:
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="player_trade",
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        counterparty="Some Trader",
        direction="exchange",
        metadata={},
        items=(
            SyncedItem(
                flow="received",
                item_id=995,
                item_name="Coins",
                quantity=received_value,
                unit_value=1,
            ),
        ),
    )


def _snapshot(occurred_at: str, skills: dict[str, int], xp: dict[str, int]) -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        captured_at=occurred_at,
        equipment=(),
        inventory=(),
        bank=(LoadoutItem(item_id=995, item_name="Coins", quantity=1_000_000, unit_value=1),),
        skills=skills,
        xp=xp,
    )


def test_an_account_with_no_companion_events_has_no_session(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    assert repository.session_summary(ACCOUNT_HASH) is None


def test_a_single_event_is_its_own_session(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.add_npc_loot_event(_loot("e1", "2026-08-25T18:00:00Z", "Vorkath", 300_000))

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.started_at == summary.ended_at
    assert summary.duration_minutes == 0
    assert summary.loot_value == 300_000
    assert summary.loot_by_npc == (LootByNpc(npc_name="Vorkath", kills=1, value=300_000),)


def test_a_gap_past_the_threshold_splits_two_sessions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    # An old session, well outside the 20-minute gap from what follows.
    repository.add_npc_loot_event(_loot("old", "2026-08-25T10:00:00Z", "Zulrah", 500_000))
    # The real session: three kills each 10 minutes apart.
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))
    repository.add_npc_loot_event(_loot("k2", "2026-08-25T18:10:00Z", "Vorkath", 250_000))
    repository.add_npc_loot_event(_loot("k3", "2026-08-25T18:20:00Z", "Vorkath", 300_000))

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.started_at == "2026-08-25T18:00:00+00:00"
    assert summary.ended_at == "2026-08-25T18:20:00+00:00"
    assert summary.duration_minutes == 20
    assert summary.loot_value == 750_000
    assert summary.loot_by_npc[0].npc_name == "Vorkath"
    assert summary.loot_by_npc[0].kills == 3


def test_deaths_and_trades_count_toward_the_same_session_as_loot(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))
    repository.add_player_death_event(_death("d1", "2026-08-25T18:05:00Z", 1_500_000))
    repository.add_synced_trade(_trade("t1", "2026-08-25T18:10:00Z", 400_000))

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.death_count == 1
    assert summary.value_lost == 1_500_000
    assert summary.trade_count == 1
    assert summary.trade_net_value == 400_000


def test_xp_gained_diffs_the_snapshot_before_the_session_against_the_one_after(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(
        _snapshot("2026-08-25T17:00:00Z", {"Attack": 75}, {"Attack": 1_210_421})
    )
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))
    repository.save_loadout_snapshot(
        _snapshot("2026-08-25T18:30:00Z", {"Attack": 76}, {"Attack": 1_250_000})
    )

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.xp_gained == {"Attack": 1_250_000 - 1_210_421}
    assert summary.levels_gained == {"Attack": 1}


def test_pre_xp_history_falls_back_to_diffing_levels(tmp_path: Path) -> None:
    """Both snapshots predate the plugin sending experience -- xp is empty on both, so the
    diff is empty too, but levels still tell a story."""
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-08-25T17:00:00Z", {"Attack": 75}, {}))
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))
    repository.save_loadout_snapshot(_snapshot("2026-08-25T18:30:00Z", {"Attack": 76}, {}))

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.xp_gained == {}
    assert summary.levels_gained == {"Attack": 1}


def test_net_worth_change_uses_the_readings_bracketing_the_session(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-08-25T17:00:00Z", {}, {}))
    first_net_worth = repository.list_net_worth_history(ACCOUNT_HASH)[-1].total_value
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))

    with_more_bank = LoadoutSnapshot(
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        captured_at="2026-08-25T18:30:00Z",
        equipment=(),
        inventory=(),
        bank=(LoadoutItem(item_id=995, item_name="Coins", quantity=2_000_000, unit_value=1),),
        skills={},
        xp={},
    )
    repository.save_loadout_snapshot(with_more_bank)
    second_net_worth = repository.list_net_worth_history(ACCOUNT_HASH)[-1].total_value

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.net_worth_change == second_net_worth - first_net_worth


def test_no_snapshot_history_at_all_leaves_the_progress_fields_empty(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.add_npc_loot_event(_loot("k1", "2026-08-25T18:00:00Z", "Vorkath", 200_000))

    summary = repository.session_summary(ACCOUNT_HASH)

    assert summary is not None
    assert summary.xp_gained == {}
    assert summary.levels_gained == {}
    assert summary.net_worth_change is None
