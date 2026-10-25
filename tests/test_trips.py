from __future__ import annotations

from datetime import UTC, datetime, timedelta

from osrs_toolkit.journal import (
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    PlayerDeathRecord,
    SkillsPoint,
)
from osrs_toolkit.trips import build_trips

BASE = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


def _at(minutes: int) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _loot(event_id: str, minutes: int, value: int, npc: str = "Vorkath") -> NpcLootRecord:
    return NpcLootRecord(
        event_id, _at(minutes), "h", "n", npc, (LoadoutItem(1, "Bones", 1, value),)
    )


def _snapshot(minutes: int, **owned: tuple[int, int]) -> LoadoutSnapshot:
    items = tuple(
        LoadoutItem(item_id, name, quantity, unit)
        for name, (item_id, quantity, unit) in (
            (name, (spec[0], spec[1], spec[2])) for name, spec in owned.items()
        )
    )
    return LoadoutSnapshot("h", "n", _at(minutes), (), items, (), {})


def _xp(minutes: int, xp: int) -> SkillsPoint:
    return SkillsPoint("h", "n", _at(minutes), {"Mining": 70}, {"Mining": xp})


def test_activity_more_than_half_an_hour_apart_is_two_trips_newest_first() -> None:
    trips = build_trips([_loot("a", 0, 100), _loot("b", 10, 100), _loot("c", 120, 50)], [], [], [])

    assert [trip.kills for trip in trips] == [1, 2]
    assert trips[1].duration_minutes == 10


def test_a_trips_loot_is_split_by_npc_with_value_per_kill() -> None:
    trip = build_trips(
        [_loot("a", 0, 300), _loot("b", 5, 100), _loot("c", 8, 40, "Zulrah")], [], [], []
    )[0]

    assert trip.loot_value == 440
    assert [npc.npc_name for npc in trip.loot_by_npc] == ["Vorkath", "Zulrah"]
    assert trip.loot_by_npc[0].value_per_kill == 200
    assert trip.title == "Vorkath, Zulrah"


def test_supplies_are_what_left_the_account_between_the_bracketing_snapshots() -> None:
    before = _snapshot(-30, Brew=(100, 40, 10), Coins=(995, 1_000, 1), Bones=(1, 5, 20))
    after = _snapshot(
        70, Brew=(100, 10, 10), Coins=(995, 10, 1), Bones=(1, 25, 20), Food=(300, 3, 5)
    )

    trip = build_trips([_loot("a", 0, 1_000), _loot("b", 25, 1_000)], [], [], [before, after])[0]

    assert [(use.item_name, use.quantity, use.value) for use in trip.supplies] == [
        ("Brew", 30, 300)
    ]
    assert trip.supplies_value == 300
    assert trip.net_profit == 1_700
    assert trip.net_per_hour == 1_700 / 25 * 60


def test_no_supplies_figure_without_a_snapshot_on_each_side() -> None:
    before = _snapshot(-30, Brew=(100, 40, 10))

    trip = build_trips([_loot("a", 0, 500)], [], [], [before])[0]

    assert trip.supplies_value is None
    assert trip.net_profit is None
    assert trip.loot_per_hour is None


def test_snapshots_from_long_before_the_trip_are_not_used() -> None:
    old = _snapshot(-60 * 20, Brew=(100, 40, 10))
    after = _snapshot(30, Brew=(100, 0, 10))

    assert build_trips([_loot("a", 0, 500)], [], [], [old, after])[0].supplies_value is None


def test_deaths_in_the_window_are_counted_with_what_was_carried() -> None:
    death = PlayerDeathRecord(
        "d", _at(4), "h", "n", False, (), (LoadoutItem(7, "Whip", 1, 1_500_000),)
    )

    trip = build_trips([_loot("a", 0, 100)], [death], [], [])[0]

    assert (trip.deaths, trip.death_value) == (1, 1_500_000)


def test_a_skilling_trip_has_no_loot_but_reports_xp_gained() -> None:
    history = [_xp(-5, 1_000), _xp(0, 2_000), _xp(20, 6_000)]

    trip = build_trips([], [], history, [])[0]

    assert trip.xp_gained == {"Mining": 5_000}
    assert trip.title == "Mining training"
    assert trip.kills == 0
