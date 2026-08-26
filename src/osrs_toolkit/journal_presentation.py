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
    """Whether an ISO timestamp falls inside the selected earnings period.

    ``now`` must be timezone-aware — its zone defines "Today"/"This week"/etc, so a naive
    stored timestamp is read as UTC (what this app writes) and converted into it.
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


def tracked_position_within_period(completed_at: str | None, period: str, now: datetime) -> bool:
    """Scope a tracked position to the selected period.

    The period filter scopes history, so an in-progress position (no completion time) stays
    in scope. Both the row list and the summary cards must use this same rule, or a
    partially sold position can show profit in one and zero in the other.
    """
    if completed_at is None:
        return True
    return trade_within_period(completed_at, period, now)


def journal_status_matches(status: str, selected_filter: str) -> bool:
    """Whether a journal row belongs in the selected status group.

    "Supplies" is a status like any other — set via the Update dialog, filtered the same
    way, and never counted toward "Active trades" since it isn't a trade in progress.
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
    """How a position's status should read, given what's actually on the GE.

    "Pending buy" covers both an offer currently filling and a flip only planned, and they
    look identical even after a cancelled offer (the plan is kept, not deleted with the
    offer). With nothing bought and no live GE slot for the item, it reads as "Planned"
    instead — only the label changes, the stored status stays untouched.

    ``placed_item_ids`` is None when there's no live view to judge against (RuneLite not
    connected, or no offer state saved yet), so nothing is relabelled then — "no offer
    found" and "nowhere to look" must not read the same.
    """
    if placed_item_ids is None or status != "Pending buy" or bought_quantity > 0:
        return status
    if item_id is None or item_id in placed_item_ids:
        return status
    return PLANNED_STATUS


# Where a position's next move is to buy vs. to sell. A status outside both (Supplies, or a
# finished flip) has no next move and so no price worth pointing at on its own.
_BUY_SIDE_STATUSES = frozenset({"Pending buy"})
_SELL_SIDE_STATUSES = frozenset({"Bought", "Listed for sale", "Partially sold"})
_SIDES = ("buy", "sell")


def next_move(status: str) -> str | None:
    """Which side of the trade a position still owes: ``"buy"``, ``"sell"``, or neither."""
    if status in _BUY_SIDE_STATUSES:
        return "buy"
    if status in _SELL_SIDE_STATUSES:
        return "sell"
    return None


def live_price_highlights(
    offers: Iterable[tuple[int, str | None]],
    candidates: Iterable[tuple[int, int | None, str]],
) -> dict[int, frozenset[str]]:
    """Which of a journal row's two prices the Grand Exchange is working on right now.

    ``offers`` is ``(item id, side)`` for everything the game currently has going: one entry
    per occupied slot, plus the "Set up offer" box if one is open. ``candidates`` is
    ``(position id, item id, stored status)`` -- the stored status, not the displayed one,
    since "Planned" is a table label rather than a real state.

    Returns position id -> the sides whose price cell should be picked out. Offers are
    unioned rather than ranked: a buy filling on one slot and a sale on another are both
    happening, so both rows stay marked, and one item bought and sold at once marks both of
    its prices. That is the difference from pointing at wherever the player happens to be
    standing, which moves for reasons the trade knows nothing about.

    A row whose own next move is the side being offered wins the offer outright. Only if no
    row for that item is on that side does the whole item take it -- an item whose only rows
    are Supplies, or finished, is still a likelier answer than nothing at all.
    """
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
            # The box is open on an item without saying which way round -- give each row the
            # price for whatever it still owes.
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
    """A listed ask the market no longer supports.

    True when the position is actively for sale and the market's current passive sell
    target has fallen at least ``threshold_pct`` below the ask. ``asking_price`` must be
    the real ask (see ``TrackedTrade.asking_price``) — passing the suggestion would flag a
    position already relisted at the market's own price, with no relist able to clear it.
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
        # Supplies mirrors its sell suggestion to its buy price (see apply_offer_opened /
        # apply_synced_ge_fill), so projecting a sale here would just show the GE tax on
        # reselling at cost — meaningless for something never meant to be sold.
        return JournalPLPresentation(
            "—",
            "neutral",
            "Marked Supplies — not tracked for profit, so there's no P/L to project.",
        )

    estimated_tone: MoneyTone = (
        "negative" if estimated_profit < 0 else "muted" if estimated_profit > 0 else "neutral"
    )
    return JournalPLPresentation(f"Est. {signed_gp(estimated_profit)}", estimated_tone)
