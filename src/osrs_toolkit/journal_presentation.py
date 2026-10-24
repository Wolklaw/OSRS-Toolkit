from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from osrs_toolkit.formatting import signed_gp

JOURNAL_STATUS_FILTERS = (
    "All statuses",
    "Active trades",
    "Pending buy",
    "Bought",
    "Listed for sale",
    "Partially sold",
    "Completed",
    "Cancelled",
    "Supplies",
)

PERIOD_FILTERS = (
    "All time",
    "Today",
    "Last 24 hours",
    "This week",
    "Last 7 days",
    "This month",
    "Last 30 days",
    "This year",
)

_ACTIVE_STATUSES = frozenset({"Pending buy", "Bought", "Listed for sale", "Partially sold"})
_COMPLETED_STATUSES = frozenset({"Completed", "Completed (manual)"})

MoneyTone = Literal["positive", "negative", "muted", "neutral"]


@dataclass(frozen=True, slots=True)
class JournalPLPresentation:
    text: str
    tone: MoneyTone
    tooltip_override: str | None = None

    @property
    def tooltip(self) -> str:
        if self.tooltip_override is not None:
            return self.tooltip_override
        if self.text == "—":
            return "Cancelled without realized sale proceeds; excluded from win rate."
        if self.text.startswith("Est. "):
            if self.tone == "negative":
                return "Projected loss from the current suggestion; not realized."
            if self.tone == "neutral":
                return "Projected break-even result; not realized."
            return "Projected P/L from the current suggestion; not realized."
        if self.tone == "positive":
            return "Realized profit from recorded sale fills."
        if self.tone == "negative":
            return "Realized loss from recorded sale fills."
        return "Realized break-even result."


def trade_within_period(timestamp: str | None, period: str, now: datetime) -> bool:
    if period == "All time":
        return True
    if timestamp is None:
        return False
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    parsed = parsed.astimezone(now.tzinfo)
    if period == "Today":
        return parsed.date() == now.date()
    if period == "Last 24 hours":
        return timedelta(0) <= now - parsed <= timedelta(hours=24)
    if period == "This week":
        start_of_week = (now - timedelta(days=now.weekday())).date()
        return start_of_week <= parsed.date() <= now.date()
    if period == "Last 7 days":
        return timedelta(0) <= now - parsed <= timedelta(days=7)
    if period == "This month":
        return parsed.year == now.year and parsed.month == now.month
    if period == "Last 30 days":
        return timedelta(0) <= now - parsed <= timedelta(days=30)
    if period == "This year":
        return parsed.year == now.year
    raise ValueError(f"Unknown period filter: {period}")


def tracked_position_within_period(completed_at: str | None, period: str, now: datetime) -> bool:
    if completed_at is None:
        return True
    return trade_within_period(completed_at, period, now)


def journal_status_matches(status: str, selected_filter: str) -> bool:
    if selected_filter == "All statuses":
        return True
    if selected_filter == "Active trades":
        return status in _ACTIVE_STATUSES
    if selected_filter in _ACTIVE_STATUSES:
        return status == selected_filter
    if selected_filter == "Completed":
        return status in _COMPLETED_STATUSES
    if selected_filter == "Cancelled":
        return status == "Cancelled"
    if selected_filter == "Supplies":
        return status == "Supplies"
    raise ValueError(f"Unknown journal status filter: {selected_filter}")


PLANNED_STATUS = "Planned"


def journal_display_status(
    status: str,
    bought_quantity: int,
    item_id: int | None,
    placed_item_ids: frozenset[int] | None,
) -> str:
    if placed_item_ids is None or status != "Pending buy" or bought_quantity > 0:
        return status
    if item_id is None or item_id in placed_item_ids:
        return status
    return PLANNED_STATUS


_BUY_SIDE_STATUSES = frozenset({"Pending buy"})
_SELL_SIDE_STATUSES = frozenset({"Bought", "Listed for sale", "Partially sold"})
_SIDES = ("buy", "sell")


def next_move(status: str) -> str | None:
    if status in _BUY_SIDE_STATUSES:
        return "buy"
    if status in _SELL_SIDE_STATUSES:
        return "sell"
    return None


def live_price_highlights(
    offers: Iterable[tuple[int, str | None]],
    candidates: Iterable[tuple[int, int | None, str]],
) -> dict[int, frozenset[str]]:
    rows = [(position_id, item_id, status) for position_id, item_id, status in candidates]
    highlights: dict[int, set[str]] = {}
    for item_id, side in offers:
        if not item_id:
            continue
        matches = [
            (position_id, status) for position_id, row_item, status in rows if row_item == item_id
        ]
        if not matches:
            continue
        if side not in _SIDES:
            for position_id, status in matches:
                move = next_move(status)
                if move is not None:
                    highlights.setdefault(position_id, set()).add(move)
            continue
        on_side = [pid for pid, status in matches if next_move(status) == side]
        for position_id in on_side or [pid for pid, _status in matches]:
            highlights.setdefault(position_id, set()).add(side)
    return {position_id: frozenset(sides) for position_id, sides in highlights.items()}


_ATTENTION_STATUSES = frozenset({"Listed for sale", "Partially sold"})


def trade_needs_attention(
    status: str,
    asking_price: int,
    live_sell_price: int | None,
    *,
    threshold_pct: float = 2.0,
) -> bool:
    if status not in _ATTENTION_STATUSES:
        return False
    if live_sell_price is None or live_sell_price <= 0 or asking_price <= 0:
        return False
    return (asking_price - live_sell_price) / asking_price * 100 >= threshold_pct


def journal_pl_presentation(
    status: str,
    estimated_profit: int,
    realized_profit: int | None,
    remaining_quantity: int = 0,
) -> JournalPLPresentation:
    if realized_profit is not None:
        text = signed_gp(realized_profit)
        if remaining_quantity > 0:
            text += f" • {remaining_quantity:,} left"
        tone: MoneyTone = (
            "positive" if realized_profit > 0 else "negative" if realized_profit < 0 else "neutral"
        )
        return JournalPLPresentation(text, tone)

    if status == "Cancelled":
        return JournalPLPresentation("—", "neutral")

    if status == "Supplies":
        return JournalPLPresentation(
            "—",
            "neutral",
            "Marked Supplies — not tracked for profit, so there's no P/L to project.",
        )

    estimated_tone: MoneyTone = (
        "negative" if estimated_profit < 0 else "muted" if estimated_profit > 0 else "neutral"
    )
    return JournalPLPresentation(f"Est. {signed_gp(estimated_profit)}", estimated_tone)
