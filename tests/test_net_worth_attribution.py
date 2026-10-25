from __future__ import annotations

from datetime import UTC, datetime, timedelta

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    PlayerDeathRecord,
)
from osrs_toolkit.net_worth_attribution import attribute_change, idle_stacks

BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _at(days: float) -> str:
    return (BASE + timedelta(days=days)).isoformat()


def _snapshot(days: float, *items: tuple[int, str, int, int]) -> LoadoutSnapshot:
    return LoadoutSnapshot(
        "h", "n", _at(days), (), tuple(LoadoutItem(*item) for item in items), (), {}
    )


COINS = (995, "Coins", 1_000_000, 1)


def test_the_parts_always_add_up_to_the_whole_change() -> None:
    start = _snapshot(0, COINS, (1, "Whip", 2, 1_000_000), (2, "Brew", 40, 10))
    end = _snapshot(7, (995, "Coins", 400_000, 1), (1, "Whip", 2, 1_200_000), (3, "Bones", 9, 5))

    result = attribute_change(start, end, [], [])

    assert result.price_movement + result.other == result.change
    assert result.price_movement == 400_000
    assert result.loot == 0 and result.deaths == 0


def test_loot_and_deaths_between_the_snapshots_are_split_out_of_the_rest() -> None:
    start = _snapshot(0, COINS)
    end = _snapshot(7, (995, "Coins", 1_600_000, 1))
    loot = [
        NpcLootRecord("a", _at(2), "h", "n", "Vorkath", (LoadoutItem(5, "Hide", 1, 800_000),)),
        NpcLootRecord("old", _at(-2), "h", "n", "Vorkath", (LoadoutItem(5, "Hide", 1, 9_000_000),)),
    ]
    deaths = [
        PlayerDeathRecord("d", _at(3), "h", "n", False, (), (LoadoutItem(7, "Whip", 1, 300_000),))
    ]

    result = attribute_change(start, end, loot, deaths)

    assert result.change == 600_000
    assert result.loot == 800_000
    assert result.deaths == -300_000
    assert result.other == 100_000
    assert result.loot + result.deaths + result.price_movement + result.other == result.change


def test_movers_are_ranked_by_how_much_they_moved_things() -> None:
    start = _snapshot(0, (1, "Whip", 1, 1_000_000), (2, "Bones", 100, 2_000))
    end = _snapshot(7, (1, "Whip", 1, 1_100_000), (2, "Bones", 100, 1_500))

    result = attribute_change(start, end, [], [])

    assert [mover.item_name for mover in result.price_movers] == ["Whip", "Bones"]
    assert result.price_movers[0].effect == 100_000
    assert result.price_movers[1].effect == -50_000


def test_a_stack_you_have_not_touched_is_idle_capital() -> None:
    earlier = _snapshot(0, (1, "Whip", 1, 1_000_000), (2, "Brew", 40, 10), (3, "Logs", 5, 100))
    later = _snapshot(
        14, (1, "Whip", 1, 1_100_000), (2, "Brew", 12, 10), (3, "Logs", 5, 100), COINS
    )

    stacks = idle_stacks(later, earlier)

    assert [stack.item_name for stack in stacks] == ["Whip"]
    assert stacks[0].value == 1_100_000


def test_the_snapshot_at_a_moment_is_the_last_one_before_it(tmp_path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    for days in (0, 3, 6):
        repository.save_loadout_snapshot(_snapshot(days, COINS))

    before = repository.get_loadout_snapshot_at("h", _at(4))
    too_early = repository.get_loadout_snapshot_at("h", _at(-1))
    fallback = repository.get_loadout_snapshot_at("h", _at(-1), fall_back_to_earliest=True)

    assert before is not None and before.captured_at == _at(3)
    assert too_early is None
    assert fallback is not None and fallback.captured_at == _at(0)
