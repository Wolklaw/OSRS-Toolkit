from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from osrs_toolkit.journal import NpcLootRecord

MINIMUM_DROPS_FOR_RATIO = 2


@dataclass(frozen=True, slots=True)
class NpcDrops:
    npc_name: str
    kills: int
    value: int
    first_kill_at: str
    last_kill_at: str


@dataclass(frozen=True, slots=True)
class DropRow:
    item_id: int
    item_name: str
    kills_with: int
    quantity: int
    unit_value: int
    rate_percent: float
    one_in: float
    kills_since: int
    longest_dry: int
    dry_ratio: float | None
    last_seen_at: str

    @property
    def total_value(self) -> int:
        return self.quantity * self.unit_value


@dataclass(frozen=True, slots=True)
class DropTable:
    npc_name: str
    kills: int
    first_kill_at: str
    last_kill_at: str
    rows: tuple[DropRow, ...]


def _moment(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def npc_summaries(events: Iterable[NpcLootRecord]) -> list[NpcDrops]:
    grouped: dict[str, list[NpcLootRecord]] = {}
    for event in events:
        grouped.setdefault(event.npc_name, []).append(event)
    summaries = [
        NpcDrops(
            npc_name=name,
            kills=len(rows),
            value=sum(event.total_value for event in rows),
            first_kill_at=min(rows, key=lambda event: _moment(event.occurred_at)).occurred_at,
            last_kill_at=max(rows, key=lambda event: _moment(event.occurred_at)).occurred_at,
        )
        for name, rows in grouped.items()
    ]
    summaries.sort(key=lambda entry: (-entry.kills, entry.npc_name))
    return summaries


def drop_table(events: Iterable[NpcLootRecord], npc_name: str) -> DropTable:
    kills: Sequence[NpcLootRecord] = sorted(
        (event for event in events if event.npc_name == npc_name),
        key=lambda event: (_moment(event.occurred_at), event.event_id),
    )
    positions: dict[int, list[int]] = {}
    quantity: dict[int, int] = {}
    names: dict[int, str] = {}
    unit_values: dict[int, int] = {}
    last_seen: dict[int, str] = {}
    for index, event in enumerate(kills):
        for item in event.items:
            names[item.item_id] = item.item_name
            unit_values[item.item_id] = item.unit_value
            quantity[item.item_id] = quantity.get(item.item_id, 0) + item.quantity
            last_seen[item.item_id] = event.occurred_at
            seen = positions.setdefault(item.item_id, [])
            if not seen or seen[-1] != index:
                seen.append(index)
    total = len(kills)
    rows = []
    for item_id, seen in positions.items():
        gaps = [later - earlier - 1 for earlier, later in pairwise([-1, *seen, total])]
        one_in = total / len(seen)
        since = total - 1 - seen[-1]
        rows.append(
            DropRow(
                item_id=item_id,
                item_name=names[item_id],
                kills_with=len(seen),
                quantity=quantity[item_id],
                unit_value=unit_values[item_id],
                rate_percent=len(seen) / total * 100,
                one_in=one_in,
                kills_since=since,
                longest_dry=max(gaps),
                dry_ratio=since / one_in if len(seen) >= MINIMUM_DROPS_FOR_RATIO else None,
                last_seen_at=last_seen[item_id],
            )
        )
    rows.sort(key=lambda row: (row.kills_with, -row.unit_value, row.item_name))
    return DropTable(
        npc_name=npc_name,
        kills=total,
        first_kill_at=kills[0].occurred_at if kills else "",
        last_kill_at=kills[-1].occurred_at if kills else "",
        rows=tuple(rows),
    )
