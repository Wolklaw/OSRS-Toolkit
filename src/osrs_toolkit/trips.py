from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from osrs_toolkit.journal import (
    SESSION_GAP_MINUTES,
    LoadoutSnapshot,
    NpcLootRecord,
    PlayerDeathRecord,
    SkillsPoint,
)

COINS_ITEM_ID = 995
BRACKET_HOURS = 6
MINIMUM_MINUTES_FOR_RATE = 5


@dataclass(frozen=True, slots=True)
class TripNpc:
    npc_name: str
    kills: int
    value: int

    @property
    def value_per_kill(self) -> int:
        return self.value // self.kills


@dataclass(frozen=True, slots=True)
class SupplyUse:
    item_name: str
    quantity: int
    value: int


@dataclass(frozen=True, slots=True)
class Trip:
    started_at: str
    ended_at: str
    duration_minutes: int
    kills: int
    loot_value: int
    loot_by_npc: tuple[TripNpc, ...]
    supplies: tuple[SupplyUse, ...]
    supplies_value: int | None
    deaths: int
    death_value: int
    xp_gained: dict[str, int]

    @property
    def net_profit(self) -> int | None:
        if self.supplies_value is None:
            return None
        return self.loot_value - self.supplies_value

    @property
    def net_per_hour(self) -> float | None:
        if self.net_profit is None or self.duration_minutes < MINIMUM_MINUTES_FOR_RATE:
            return None
        return self.net_profit / self.duration_minutes * 60

    @property
    def loot_per_hour(self) -> float | None:
        if self.duration_minutes < MINIMUM_MINUTES_FOR_RATE:
            return None
        return self.loot_value / self.duration_minutes * 60

    @property
    def title(self) -> str:
        if self.loot_by_npc:
            return ", ".join(npc.npc_name for npc in self.loot_by_npc[:2])
        if self.xp_gained:
            top = max(self.xp_gained, key=lambda skill: self.xp_gained[skill])
            return f"{top} training"
        return "Activity"


def _moment(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _xp_moments(history: Sequence[SkillsPoint]) -> list[datetime]:
    moments = []
    for before, after in pairwise(history):
        if before.xp != after.xp:
            moments.append(_moment(after.captured_at))
    return moments


def _owned(snapshot: LoadoutSnapshot) -> dict[int, tuple[str, int, int]]:
    owned: dict[int, tuple[str, int, int]] = {}
    for item in (*snapshot.equipment, *snapshot.inventory, *snapshot.bank):
        name, quantity, _unit = owned.get(item.item_id, (item.item_name, 0, item.unit_value))
        owned[item.item_id] = (name, quantity + item.quantity, item.unit_value)
    return owned


def _supplies_used(
    snapshots: Sequence[LoadoutSnapshot], started: datetime, ended: datetime
) -> tuple[SupplyUse, ...] | None:
    window = timedelta(hours=BRACKET_HOURS)
    before = next(
        (
            snapshot
            for snapshot in reversed(snapshots)
            if started - window <= _moment(snapshot.captured_at) <= started
        ),
        None,
    )
    after = next(
        (
            snapshot
            for snapshot in snapshots
            if ended <= _moment(snapshot.captured_at) <= ended + window
        ),
        None,
    )
    if before is None or after is None:
        return None
    start_items, end_items = _owned(before), _owned(after)
    used = []
    for item_id, (name, quantity, unit_value) in start_items.items():
        if item_id == COINS_ITEM_ID:
            continue
        lost = quantity - end_items.get(item_id, (name, 0, unit_value))[1]
        if lost > 0:
            used.append(SupplyUse(name, lost, lost * unit_value))
    used.sort(key=lambda entry: (-entry.value, entry.item_name))
    return tuple(used)


def build_trips(
    loot: Iterable[NpcLootRecord],
    deaths: Iterable[PlayerDeathRecord],
    skills_history: Sequence[SkillsPoint],
    snapshots: Sequence[LoadoutSnapshot],
) -> list[Trip]:
    loot_events = sorted(loot, key=lambda event: _moment(event.occurred_at))
    death_events = sorted(deaths, key=lambda event: _moment(event.occurred_at))
    ordered_snapshots = sorted(snapshots, key=lambda snapshot: _moment(snapshot.captured_at))
    history = sorted(skills_history, key=lambda point: _moment(point.captured_at))
    moments = sorted(
        [_moment(event.occurred_at) for event in (*loot_events, *death_events)]
        + _xp_moments(history)
    )
    if not moments:
        return []

    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    windows: list[tuple[datetime, datetime]] = []
    start = end = moments[0]
    for moment in moments[1:]:
        if moment - end > gap:
            windows.append((start, end))
            start = moment
        end = moment
    windows.append((start, end))

    trips = []
    for started, ended in windows:
        in_loot = [e for e in loot_events if started <= _moment(e.occurred_at) <= ended]
        in_deaths = [e for e in death_events if started <= _moment(e.occurred_at) <= ended]
        by_npc: dict[str, list[int]] = {}
        for event in in_loot:
            entry = by_npc.setdefault(event.npc_name, [0, 0])
            entry[0] += 1
            entry[1] += event.total_value
        npcs = tuple(
            sorted(
                (TripNpc(name, kills, value) for name, (kills, value) in by_npc.items()),
                key=lambda npc: (-npc.value, npc.npc_name),
            )
        )
        supplies = _supplies_used(ordered_snapshots, started, ended)
        baseline = next((p for p in reversed(history) if _moment(p.captured_at) < started), None)
        endpoint = next((p for p in reversed(history) if _moment(p.captured_at) <= ended), None)
        xp_gained: dict[str, int] = {}
        if baseline is not None and endpoint is not None:
            for skill, value in endpoint.xp.items():
                gained = value - baseline.xp.get(skill, value)
                if gained > 0:
                    xp_gained[skill] = gained
        trips.append(
            Trip(
                started_at=started.isoformat(),
                ended_at=ended.isoformat(),
                duration_minutes=round((ended - started).total_seconds() / 60),
                kills=len(in_loot),
                loot_value=sum(event.total_value for event in in_loot),
                loot_by_npc=npcs,
                supplies=supplies or (),
                supplies_value=None if supplies is None else sum(s.value for s in supplies),
                deaths=len(in_deaths),
                death_value=sum(event.total_value for event in in_deaths),
                xp_gained=xp_gained,
            )
        )
    trips.reverse()
    return trips
