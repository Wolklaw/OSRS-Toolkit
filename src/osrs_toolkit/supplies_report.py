from __future__ import annotations

from dataclasses import dataclass

from osrs_toolkit.journal import TrackedTrade


@dataclass(frozen=True, slots=True)
class SuppliesSpendRow:
    item_name: str
    quantity: int
    spent: int
    purchases: int
    last_bought: str


def supplies_spend_rows(tracked: list[TrackedTrade]) -> list[SuppliesSpendRow]:
    groups: dict[str, list[TrackedTrade]] = {}
    for trade in tracked:
        if trade.status != "Supplies":
            continue
        groups.setdefault(trade.item_name, []).append(trade)
    rows = [
        SuppliesSpendRow(
            item_name=item_name,
            quantity=sum(trade.quantity for trade in trades),
            spent=sum(trade.invested for trade in trades),
            purchases=len(trades),
            last_bought=max(trade.created_at for trade in trades),
        )
        for item_name, trades in groups.items()
    ]
    rows.sort(key=lambda row: row.spent, reverse=True)
    return rows


def total_supplies_spend(tracked: list[TrackedTrade]) -> int:
    return sum(trade.invested for trade in tracked if trade.status == "Supplies")
