from __future__ import annotations

from datetime import UTC, datetime, timedelta

from osrs_toolkit.drops import drop_table, npc_summaries
from osrs_toolkit.journal import LoadoutItem, NpcLootRecord

BASE = datetime(2026, 10, 1, tzinfo=UTC)


def _kill(index: int, *items: tuple[int, str, int, int], npc: str = "Vorkath") -> NpcLootRecord:
    return NpcLootRecord(
        event_id=f"k{index}-{npc}",
        occurred_at=(BASE + timedelta(minutes=index)).isoformat(),
        account_hash="h",
        account_name="n",
        npc_name=npc,
        items=tuple(LoadoutItem(*item) for item in items),
    )


BONES = (1, "Dragon bones", 2, 2_000)
VISAGE = (9, "Draconic visage", 1, 5_000_000)


def test_npcs_are_listed_most_killed_first_with_loot_value() -> None:
    events = [_kill(0, BONES), _kill(1, BONES), _kill(2, BONES, npc="Zulrah")]

    summaries = npc_summaries(events)

    assert [(entry.npc_name, entry.kills) for entry in summaries] == [("Vorkath", 2), ("Zulrah", 1)]
    assert summaries[0].value == 8_000


def test_a_drops_rate_and_one_in_come_from_kills_it_appeared_in() -> None:
    events = [_kill(index, BONES) for index in range(10)]
    events[3] = _kill(3, BONES, VISAGE)

    table = drop_table(events, "Vorkath")
    visage = next(row for row in table.rows if row.item_name == "Draconic visage")

    assert table.kills == 10
    assert visage.kills_with == 1
    assert visage.rate_percent == 10.0
    assert visage.one_in == 10.0
    assert visage.quantity == 1


def test_dry_streaks_count_kills_since_the_last_drop_and_the_longest_gap() -> None:
    events = [_kill(index, BONES) for index in range(12)]
    events[2] = _kill(2, BONES, VISAGE)
    events[5] = _kill(5, BONES, VISAGE)

    visage = next(row for row in drop_table(events, "Vorkath").rows if row.item_id == 9)

    assert visage.kills_with == 2
    assert visage.kills_since == 6
    assert visage.longest_dry == 6
    assert visage.dry_ratio == 6 / 6


def test_the_dry_ratio_waits_for_a_second_drop() -> None:
    events = [_kill(0, BONES, VISAGE), _kill(1, BONES), _kill(2, BONES)]

    visage = next(row for row in drop_table(events, "Vorkath").rows if row.item_id == 9)

    assert visage.dry_ratio is None
    assert visage.kills_since == 2


def test_rarest_items_come_first_and_other_monsters_are_ignored() -> None:
    events = [_kill(index, BONES) for index in range(4)]
    events[1] = _kill(1, BONES, VISAGE)
    events.append(_kill(9, (7, "Zulrah scales", 100, 1), npc="Zulrah"))

    table = drop_table(events, "Vorkath")

    assert [row.item_name for row in table.rows] == ["Draconic visage", "Dragon bones"]
    assert table.kills == 4
