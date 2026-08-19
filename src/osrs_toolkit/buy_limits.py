"""How much of each item's 4-hour Grand Exchange buy limit is still available.

OSRS buy limits are a rolling 4-hour window, not a fixed reset time: each purchase counts
against the limit until exactly 4 hours after it happened, then drops back out on its own.
Built from the synced GE fills this app already records, so it needs nothing new from the
RuneLite plugin. Kept free of Qt so it can be tested directly the way ``journal_presentation``
and ``performance`` are.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from osrs_toolkit.journal import SyncedTrade

BUY_LIMIT_WINDOW = timedelta(hours=4)


@dataclass(frozen=True, slots=True)
class BuyLimitStatus:
    item_id: int
    item_name: str
    limit: int
    bought_recently: int
    resets_at: str

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.bought_recently)


def buy_limit_status(
    trades: list[SyncedTrade],
    limits: Mapping[int, int],
    now: datetime,
) -> list[BuyLimitStatus]:
    """One row per item bought within the last 4 hours, sorted by least room left first.

    ``trades`` is expected to be every synced GE fill, buys and sells alike; anything that
    isn't a buy-side ``ge_fill`` is ignored. Items with no known buy limit are skipped —
    there's nothing useful to say about their remaining room. ``resets_at`` is when the
    *oldest* purchase still inside the window ages out, which is the next moment the
    remaining count can go up; later purchases within the window free up after that.
    """
    window_start = now - BUY_LIMIT_WINDOW
    quantity_by_item: dict[int, int] = {}
    name_by_item: dict[int, str] = {}
    earliest_by_item: dict[int, datetime] = {}
    for trade in trades:
        if trade.event_type != "ge_fill" or trade.direction != "buy" or not trade.received:
            continue
        occurred = _instant(trade.occurred_at)
        if occurred < window_start:
            continue
        item = trade.received[0]
        quantity_by_item[item.item_id] = quantity_by_item.get(item.item_id, 0) + item.quantity
        name_by_item[item.item_id] = item.item_name
        if item.item_id not in earliest_by_item or occurred < earliest_by_item[item.item_id]:
            earliest_by_item[item.item_id] = occurred

    statuses = [
        BuyLimitStatus(
            item_id=item_id,
            item_name=name_by_item[item_id],
            limit=limits[item_id],
            bought_recently=bought,
            resets_at=(earliest_by_item[item_id] + BUY_LIMIT_WINDOW).isoformat(timespec="seconds"),
        )
        for item_id, bought in quantity_by_item.items()
        if item_id in limits
    ]
    statuses.sort(key=lambda status: (status.remaining, status.resets_at))
    return statuses


def _instant(raw: str) -> datetime:
    """A stored occurred_at, as a comparable instant. A value without a zone can't be
    compared with one that has it, hence the UTC default — matches how sync events are
    ordered elsewhere in this app."""
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
