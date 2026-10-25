from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from osrs_toolkit.journal import OfferEventRecord
from osrs_toolkit.journal_presentation import trade_within_period

UNASSIGNED_STRATEGY = "No strategy"
MINIMUM_OFFERS_FOR_RATE = 5


@dataclass(frozen=True, slots=True)
class CancellationGroup:
    label: str
    side: str
    opened: int
    cancelled: int
    matched: int
    cancel_rate: float | None
    median_wait_minutes: float | None


@dataclass(frozen=True, slots=True)
class CancellationSummary:
    opened: int
    cancelled: int
    median_wait_minutes: float | None


@dataclass(frozen=True, slots=True)
class _Cancellation:
    event: OfferEventRecord
    strategy: str
    wait_minutes: float | None


def _moment(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _match_cancellations(events: Iterable[OfferEventRecord]) -> list[_Cancellation]:
    openings: dict[tuple, list[OfferEventRecord]] = {}
    matched: list[_Cancellation] = []
    for event in sorted(events, key=lambda row: (_moment(row.occurred_at), row.event_id)):
        key = (
            event.account_hash,
            event.side,
            event.item_id,
            event.offer_price,
            event.total_quantity,
        )
        if event.kind == "opened":
            openings.setdefault(key, []).append(event)
            continue
        stack = openings.get(key)
        opening = stack.pop() if stack else None
        wait = None
        if opening is not None and not opening.restored:
            wait = (_moment(event.occurred_at) - _moment(opening.occurred_at)).total_seconds() / 60
        strategy = event.strategy or (opening.strategy if opening else None)
        matched.append(_Cancellation(event, strategy or UNASSIGNED_STRATEGY, wait))
    return matched


def cancellation_groups(
    events: Iterable[OfferEventRecord], period: str, now: datetime
) -> list[CancellationGroup]:
    rows = list(events)
    cancellations = [
        item
        for item in _match_cancellations(rows)
        if trade_within_period(item.event.occurred_at, period, now)
    ]
    opened: dict[tuple[str, str], int] = {}
    for event in rows:
        if (
            event.kind == "opened"
            and not event.restored
            and trade_within_period(event.occurred_at, period, now)
        ):
            key = (event.strategy or UNASSIGNED_STRATEGY, event.side)
            opened[key] = opened.get(key, 0) + 1
    cancelled: dict[tuple[str, str], list[_Cancellation]] = {}
    for item in cancellations:
        cancelled.setdefault((item.strategy, item.event.side), []).append(item)
    groups = []
    for key in sorted(set(opened) | set(cancelled)):
        label, side = key
        items = cancelled.get(key, [])
        waits = [item.wait_minutes for item in items if item.wait_minutes is not None]
        opened_count = opened.get(key, 0)
        rate = (
            min(100.0, len(waits) / opened_count * 100)
            if opened_count >= MINIMUM_OFFERS_FOR_RATE
            else None
        )
        groups.append(
            CancellationGroup(
                label=label,
                side=side,
                opened=opened_count,
                cancelled=len(items),
                matched=len(waits),
                cancel_rate=rate,
                median_wait_minutes=median(waits) if waits else None,
            )
        )
    groups.sort(key=lambda group: (-group.cancelled, group.label, group.side))
    return groups


def summarize_cancellations(groups: Iterable[CancellationGroup]) -> CancellationSummary:
    rows = list(groups)
    waits = [
        group.median_wait_minutes
        for group in rows
        for _ in range(group.matched)
        if group.median_wait_minutes is not None
    ]
    return CancellationSummary(
        opened=sum(group.opened for group in rows),
        cancelled=sum(group.cancelled for group in rows),
        median_wait_minutes=median(waits) if waits else None,
    )
