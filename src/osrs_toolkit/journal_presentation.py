from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

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

_ACTIVE_STATUSES = frozenset(
    {"Pending buy", "Bought", "Listed for sale", "Partially sold"}
)
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


def _signed_gp(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,} gp"


def trade_within_period(timestamp: str | None, period: str, now: datetime) -> bool:
    """Return whether an ISO timestamp falls inside the selected earnings period.

    ``now`` must be timezone-aware, and its timezone defines the calendar: "Today",
    "This week", "This month", and "This year" are boundaries in *that* zone, so passing
    a local ``now`` makes them mean the user's day rather than the UTC day. Stored
    timestamps are converted into it before any date comparison. A naive stored timestamp
    is read as UTC, since that is what this app writes (``datetime.now(UTC)``).
    """
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


def tracked_position_within_period(
    completed_at: str | None, period: str, now: datetime
) -> bool:
    """Scope a tracked position to the selected period.

    The period filter scopes *history*, so it only applies to positions that have finished.
    A position still in progress has no completion time to place in any period, so it stays
    in scope: its row keeps showing and the profit already realized from its recorded sale
    fills keeps counting toward the summary cards. Both callers must use this one rule —
    scoping the rows and the cards differently is what let a partially sold position
    display "+22,500 gp" above a summary card reading zero.
    """
    if completed_at is None:
        return True
    return trade_within_period(completed_at, period, now)


def journal_status_matches(status: str, selected_filter: str) -> bool:
    """Return whether a journal row belongs in the selected status group.

    "Supplies" is a status like any other here — a quest or skilling buy the player has
    said isn't a flip, set the same way as any other status change (the Update dialog),
    and filtered the same way. It never counts toward "Active trades": it isn't a trade in
    progress, it's an entry excluded from trading altogether.
    """
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
    """How a position's status should read, given what is actually on the Grand Exchange.

    "Pending buy" says two different things at once: an offer is out there filling, and a
    flip is planned but not placed. They look identical in the table, and they stay that way
    after a cancelled offer — a plan the player made is deliberately kept rather than deleted
    with the offer that was placed for it, so the row outlives the offer that explains it.
    A pending buy with nothing bought and no Grand Exchange slot holding a buy for that item
    reads as "Planned" instead. Only the label changes: the stored status is untouched, so
    filters, sorting and the Update dialog all still see one status.

    ``placed_item_ids`` is None when there is no live view of the slots to judge against —
    RuneLite not connected, or no offer state saved yet. Nothing is relabelled then, because
    "no offer found" and "nowhere to look" must not read the same.
    """
    if placed_item_ids is None or status != "Pending buy" or bought_quantity > 0:
        return status
    if item_id is None or item_id in placed_item_ids:
        return status
    return PLANNED_STATUS


_ATTENTION_STATUSES = frozenset({"Listed for sale", "Partially sold"})


def trade_needs_attention(
    status: str,
    asking_price: int,
    live_sell_price: int | None,
    *,
    threshold_pct: float = 2.0,
) -> bool:
    """A listed ask that current market conditions no longer support.

    True when a position is actively for sale and the market's current passive sell
    target has fallen at least ``threshold_pct`` below what this position is asking —
    meaning the ask is stale and unlikely to fill soon.

    ``asking_price`` must be what the position is really asking, not what the app suggests
    it ask: see ``TrackedTrade.asking_price``. Passing the suggestion flags a position the
    player has already relisted at the market's own price, and no relist can clear it.
    """
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
    """Describe journal P/L without depending on the desktop UI toolkit."""
    if realized_profit is not None:
        text = _signed_gp(realized_profit)
        if remaining_quantity > 0:
            text += f" • {remaining_quantity:,} left"
        tone: MoneyTone = (
            "positive"
            if realized_profit > 0
            else "negative"
            if realized_profit < 0
            else "neutral"
        )
        return JournalPLPresentation(text, tone)

    if status == "Cancelled":
        return JournalPLPresentation("—", "neutral")

    if status == "Supplies":
        # A Supplies position's sell suggestion mirrors its buy price (see
        # apply_offer_opened/apply_synced_ge_fill), so projecting a sale here would show
        # roughly "the GE tax you'd pay if you resold it at cost" — an alarming, meaningless
        # number for something that was never going to be sold in the first place.
        return JournalPLPresentation(
            "—", "neutral", "Marked Supplies — not tracked for profit, so there's no P/L to project."
        )

    estimated_tone: MoneyTone = (
        "negative" if estimated_profit < 0 else "muted" if estimated_profit > 0 else "neutral"
    )
    return JournalPLPresentation(f"Est. {_signed_gp(estimated_profit)}", estimated_tone)
