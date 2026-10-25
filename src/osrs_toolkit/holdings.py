from __future__ import annotations

from dataclasses import dataclass

from osrs_toolkit.journal import LoadoutSnapshot, TrackedTrade
from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax, is_tax_exempt, offer_targets

COINS_ITEM_ID = 995
HELD_STATUS = "Bought"


@dataclass(frozen=True, slots=True)
class UntrackedHolding:
    item_id: int
    item_name: str
    quantity: int
    suggested_sell: int
    net_each: int

    @property
    def net_total(self) -> int:
        return self.net_each * self.quantity


def untracked_holdings(
    snapshot: LoadoutSnapshot | None,
    tracked: list[TrackedTrade],
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
) -> list[UntrackedHolding]:
    if snapshot is None:
        return []
    point_by_id = {point.item_id: point for point in points}
    accounted: dict[int, int] = {}
    for trade in tracked:
        if trade.status == HELD_STATUS and trade.item_id is not None:
            accounted[trade.item_id] = accounted.get(trade.item_id, 0) + trade.unsold_stock
    carried: dict[int, int] = {}
    for item in (*snapshot.inventory, *snapshot.bank):
        carried[item.item_id] = carried.get(item.item_id, 0) + item.quantity
    rows: list[UntrackedHolding] = []
    for item_id, quantity in carried.items():
        point = point_by_id.get(item_id)
        mapping = mappings.get(item_id)
        if item_id == COINS_ITEM_ID or point is None or mapping is None:
            continue
        untracked = quantity - accounted.get(item_id, 0)
        if untracked <= 0:
            continue
        try:
            _buy, sell = offer_targets(point)
        except ValueError:
            continue
        tax = ge_tax(sell, exempt=is_tax_exempt(item_id, mapping.name))
        rows.append(
            UntrackedHolding(
                item_id=item_id,
                item_name=mapping.name,
                quantity=untracked,
                suggested_sell=sell,
                net_each=sell - tax,
            )
        )
    rows.sort(key=lambda row: row.net_total, reverse=True)
    return rows
