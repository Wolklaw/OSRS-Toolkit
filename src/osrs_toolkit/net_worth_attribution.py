from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from osrs_toolkit.journal import LoadoutSnapshot, NpcLootRecord, PlayerDeathRecord

COINS_ITEM_ID = 995
MOVER_LIMIT = 8
IDLE_MINIMUM_VALUE = 100_000


@dataclass(frozen=True, slots=True)
class PriceMover:
    item_name: str
    quantity: int
    price_before: int
    price_after: int
    effect: int


@dataclass(frozen=True, slots=True)
class QuantityMover:
    item_name: str
    quantity_change: int
    effect: int


@dataclass(frozen=True, slots=True)
class Attribution:
    started_at: str
    ended_at: str
    start_value: int
    end_value: int
    price_movement: int
    loot: int
    deaths: int
    other: int
    price_movers: tuple[PriceMover, ...]
    quantity_movers: tuple[QuantityMover, ...]

    @property
    def change(self) -> int:
        return self.end_value - self.start_value


@dataclass(frozen=True, slots=True)
class IdleStack:
    item_name: str
    quantity: int
    value: int


def _moment(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _holdings(snapshot: LoadoutSnapshot) -> dict[int, tuple[str, int, int]]:
    held: dict[int, tuple[str, int, int]] = {}
    for item in (*snapshot.equipment, *snapshot.inventory, *snapshot.bank):
        name, quantity, _unit = held.get(item.item_id, (item.item_name, 0, item.unit_value))
        held[item.item_id] = (name, quantity + item.quantity, item.unit_value)
    return held


def attribute_change(
    start: LoadoutSnapshot,
    end: LoadoutSnapshot,
    loot: Iterable[NpcLootRecord],
    deaths: Iterable[PlayerDeathRecord],
) -> Attribution:
    before, after = _holdings(start), _holdings(end)
    began, finished = _moment(start.captured_at), _moment(end.captured_at)
    price_effects: list[PriceMover] = []
    quantity_effects: list[QuantityMover] = []
    price_total = quantity_total = 0
    for item_id in before.keys() | after.keys():
        name_before, qty_before, price_before = before.get(item_id, ("", 0, 0))
        name_after, qty_after, price_after = after.get(item_id, ("", 0, 0))
        name = name_after or name_before
        end_price = price_after if item_id in after else price_before
        if item_id in before and item_id in after:
            effect = qty_before * (price_after - price_before)
            price_total += effect
            if effect:
                price_effects.append(
                    PriceMover(name, qty_before, price_before, price_after, effect)
                )
        quantity_effect = (qty_after - qty_before) * end_price
        quantity_total += quantity_effect
        if quantity_effect:
            quantity_effects.append(QuantityMover(name, qty_after - qty_before, quantity_effect))
    loot_value = sum(
        event.total_value for event in loot if began < _moment(event.occurred_at) <= finished
    )
    death_value = sum(
        event.total_value for event in deaths if began < _moment(event.occurred_at) <= finished
    )
    price_effects.sort(key=lambda mover: (-abs(mover.effect), mover.item_name))
    quantity_effects.sort(key=lambda mover: (-abs(mover.effect), mover.item_name))
    return Attribution(
        started_at=start.captured_at,
        ended_at=end.captured_at,
        start_value=sum(qty * price for _name, qty, price in before.values()),
        end_value=sum(qty * price for _name, qty, price in after.values()),
        price_movement=price_total,
        loot=loot_value,
        deaths=-death_value,
        other=quantity_total - loot_value + death_value,
        price_movers=tuple(price_effects[:MOVER_LIMIT]),
        quantity_movers=tuple(quantity_effects[:MOVER_LIMIT]),
    )


def idle_stacks(
    latest: LoadoutSnapshot,
    reference: LoadoutSnapshot,
    minimum_value: int = IDLE_MINIMUM_VALUE,
) -> list[IdleStack]:
    now_held, then_held = _holdings(latest), _holdings(reference)
    stacks = []
    for item_id, (name, quantity, price) in now_held.items():
        if item_id == COINS_ITEM_ID or item_id not in then_held:
            continue
        if then_held[item_id][1] != quantity or quantity * price < minimum_value:
            continue
        stacks.append(IdleStack(name, quantity, quantity * price))
    stacks.sort(key=lambda stack: (-stack.value, stack.item_name))
    return stacks
