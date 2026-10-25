from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from osrs_toolkit.journal import OfferEventRecord, SyncedTrade
from osrs_toolkit.journal_presentation import trade_within_period

FILLED_STATES = frozenset({"BOUGHT", "SOLD"})
DEFAULT_MINIMUM_OFFERS = 3


@dataclass(frozen=True, slots=True)
class FillObservation:
    item_name: str
    side: str
    offer_price: int
    completed_at: str
    first_fill_minutes: float
    complete_minutes: float


@dataclass(frozen=True, slots=True)
class FillTimeGroup:
    label: str
    side: str
    offers: int
    median_first_fill_minutes: float
    median_complete_minutes: float
    fastest_minutes: float
    slowest_minutes: float


@dataclass(frozen=True, slots=True)
class _Fill:
    at: datetime
    account_hash: str
    side: str
    item_id: int
    offer_price: int
    total_quantity: int
    final: bool


@dataclass(slots=True)
class _Open:
    event: OfferEventRecord
    opened_at: datetime
    first_fill_at: datetime | None = None


def _moment(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _fill(trade: SyncedTrade) -> _Fill | None:
    if trade.event_type != "ge_fill" or trade.direction not in {"buy", "sell"}:
        return None
    goods = next((item for item in trade.items if item.item_id != 995), None)
    offer_price = _positive_int(trade.metadata.get("offer_price"))
    total_quantity = _positive_int(trade.metadata.get("total_quantity"))
    if goods is None or offer_price is None or total_quantity is None:
        return None
    return _Fill(
        at=_moment(trade.occurred_at),
        account_hash=trade.account_hash,
        side=trade.direction,
        item_id=goods.item_id,
        offer_price=offer_price,
        total_quantity=total_quantity,
        final=trade.metadata.get("offer_state") in FILLED_STATES,
    )


def _key(account_hash: str, side: str, item_id: int, price: int, quantity: int) -> tuple:
    return (account_hash, side, item_id, price, quantity)


def fill_observations(
    events: Iterable[OfferEventRecord], trades: Iterable[SyncedTrade]
) -> list[FillObservation]:
    timeline: list[tuple[datetime, int, object]] = []
    for event in events:
        if event.kind == "opened" and event.restored:
            continue
        timeline.append((_moment(event.occurred_at), 0 if event.kind == "opened" else 2, event))
    for trade in trades:
        fill = _fill(trade)
        if fill is not None:
            timeline.append((fill.at, 1, fill))
    timeline.sort(key=lambda row: (row[0], row[1]))

    open_offers: dict[tuple, _Open] = {}
    observations: list[FillObservation] = []
    for moment, _rank, entry in timeline:
        if isinstance(entry, OfferEventRecord):
            key = _key(
                entry.account_hash,
                entry.side,
                entry.item_id,
                entry.offer_price,
                entry.total_quantity,
            )
            if entry.kind == "opened":
                open_offers[key] = _Open(entry, moment)
            else:
                open_offers.pop(key, None)
            continue
        key = _key(
            entry.account_hash, entry.side, entry.item_id, entry.offer_price, entry.total_quantity
        )
        current = open_offers.get(key)
        if current is None:
            continue
        if current.first_fill_at is None:
            current.first_fill_at = moment
        if entry.final:
            open_offers.pop(key)
            observations.append(
                FillObservation(
                    item_name=current.event.item_name,
                    side=current.event.side,
                    offer_price=current.event.offer_price,
                    completed_at=moment.isoformat(),
                    first_fill_minutes=(current.first_fill_at - current.opened_at).total_seconds()
                    / 60,
                    complete_minutes=(moment - current.opened_at).total_seconds() / 60,
                )
            )
    return observations


def fill_time_groups(
    observations: Iterable[FillObservation],
    period: str,
    now: datetime,
    minimum_offers: int = DEFAULT_MINIMUM_OFFERS,
) -> list[FillTimeGroup]:
    grouped: dict[tuple[str, str], list[FillObservation]] = {}
    for observation in observations:
        if trade_within_period(observation.completed_at, period, now):
            grouped.setdefault((observation.item_name, observation.side), []).append(observation)
    groups = [
        FillTimeGroup(
            label=label,
            side=side,
            offers=len(rows),
            median_first_fill_minutes=median(row.first_fill_minutes for row in rows),
            median_complete_minutes=median(row.complete_minutes for row in rows),
            fastest_minutes=min(row.complete_minutes for row in rows),
            slowest_minutes=max(row.complete_minutes for row in rows),
        )
        for (label, side), rows in grouped.items()
        if len(rows) >= minimum_offers
    ]
    groups.sort(key=lambda group: (-group.offers, group.label, group.side))
    return groups
