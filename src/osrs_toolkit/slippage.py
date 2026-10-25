from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from osrs_toolkit.journal import SyncedTrade
from osrs_toolkit.journal_presentation import trade_within_period

DEFAULT_MINIMUM_FILLS = 3


@dataclass(frozen=True, slots=True)
class FillSlippage:
    item_id: int
    item_name: str
    side: str
    quantity: int
    offer_price: int
    coins: int
    occurred_at: str

    @property
    def offer_value(self) -> int:
        return self.offer_price * self.quantity

    @property
    def improvement(self) -> int:
        if self.side == "buy":
            return self.offer_value - self.coins
        return self.coins - self.offer_value


@dataclass(frozen=True, slots=True)
class SlippageGroup:
    label: str
    side: str
    fills: int
    quantity: int
    offer_value: int
    improvement: int

    @property
    def improvement_per_unit(self) -> float:
        return self.improvement / self.quantity

    @property
    def improvement_percent(self) -> float | None:
        if self.offer_value <= 0:
            return None
        return self.improvement / self.offer_value * 100


@dataclass(frozen=True, slots=True)
class SlippageSummary:
    fills: int
    quantity: int
    buy_improvement: int
    sell_improvement: int

    @property
    def total_improvement(self) -> int:
        return self.buy_improvement + self.sell_improvement


def fill_slippage(trade: SyncedTrade) -> FillSlippage | None:
    if trade.event_type != "ge_fill" or trade.direction not in {"buy", "sell"}:
        return None
    offer_price = trade.metadata.get("offer_price")
    if isinstance(offer_price, bool) or not isinstance(offer_price, int) or offer_price <= 0:
        return None
    goods = next(
        (item for item in trade.items if item.item_id != 995),
        None,
    )
    money = next((item for item in trade.items if item.item_id == 995), None)
    if goods is None or money is None:
        return None
    return FillSlippage(
        item_id=goods.item_id,
        item_name=goods.item_name,
        side=trade.direction,
        quantity=goods.quantity,
        offer_price=offer_price,
        coins=money.quantity,
        occurred_at=trade.occurred_at,
    )


def slippage_fills(trades: Iterable[SyncedTrade], period: str, now: datetime) -> list[FillSlippage]:
    fills = []
    for trade in trades:
        fill = fill_slippage(trade)
        if fill is not None and trade_within_period(fill.occurred_at, period, now):
            fills.append(fill)
    return fills


def summarize_slippage(fills: Iterable[FillSlippage]) -> SlippageSummary:
    rows = list(fills)
    return SlippageSummary(
        fills=len(rows),
        quantity=sum(fill.quantity for fill in rows),
        buy_improvement=sum(fill.improvement for fill in rows if fill.side == "buy"),
        sell_improvement=sum(fill.improvement for fill in rows if fill.side == "sell"),
    )


def _group(fills: Iterable[FillSlippage], key) -> list[tuple[str, str, list[FillSlippage]]]:
    grouped: dict[tuple[str, str], list[FillSlippage]] = {}
    for fill in fills:
        grouped.setdefault(key(fill), []).append(fill)
    return [(label, side, rows) for (label, side), rows in grouped.items()]


def by_item_slippage(
    fills: Iterable[FillSlippage], minimum_fills: int = DEFAULT_MINIMUM_FILLS
) -> list[SlippageGroup]:
    groups = [
        SlippageGroup(
            label=label,
            side=side,
            fills=len(rows),
            quantity=sum(fill.quantity for fill in rows),
            offer_value=sum(fill.offer_value for fill in rows),
            improvement=sum(fill.improvement for fill in rows),
        )
        for label, side, rows in _group(fills, lambda fill: (fill.item_name, fill.side))
        if len(rows) >= minimum_fills
    ]
    groups.sort(key=lambda group: (-abs(group.improvement), group.label, group.side))
    return groups
