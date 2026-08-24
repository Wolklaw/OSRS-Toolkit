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


# Where a position's next move is to buy vs. to sell — a row is a candidate for the open
# offer box on whichever side it still has something left to do.
_BUY_SIDE_STATUSES = frozenset({"Pending buy"})
_SELL_SIDE_STATUSES = frozenset({"Bought", "Listed for sale", "Partially sold"})


def offer_screen_is_sell_side(status: str) -> bool:
    """Whether a position needs a sale next rather than a buy — decides which price
    column the highlight belongs on."""
    return status in _SELL_SIDE_STATUSES


def offer_screen_positions(
    item_id: int | None,
    side: str | None,
    candidates: Iterable[tuple[int, int | None, str]],
) -> frozenset[int]:
    """The journal rows an open Grand Exchange offer box is about.

    ``candidates`` is ``(position id, item id, stored status)``; the stored status, not the
    displayed one, since "Planned" is a table label rather than a real state.

    Narrows to rows whose next move is the side being offered, but falls back to every row
    for the item if none match — an item whose only rows are on the other side (or in a
    status with no side, like Supplies) should still light up, since that's likelier to be
    the wanted row than nothing at all. Several rows for one item all return; picking one
    would mean guessing which the player means.
    """
    if not item_id:
        return frozenset()
    matches = {
        position_id: status
        for position_id, candidate_item_id, status in candidates
        if candidate_item_id == item_id
    }
    wanted = (
        _BUY_SIDE_STATUSES if side == "buy" else _SELL_SIDE_STATUSES if side == "sell" else None
    )
    if wanted is not None:
        on_side = frozenset(
            position_id for position_id, status in matches.items() if status in wanted
        )
        if on_side:
            return on_side
    return frozenset(matches)


def live_offer_positions(
    live_offers: Iterable[tuple[int, str | None]],
    candidates: Iterable[tuple[int, int | None, str]],
) -> frozenset[int]:
    """The journal rows the offers already out on the GE are about.

    What the highlight falls back to once a box is confirmed and the interface no longer
    names an item, only the slots do. ``live_offers`` is ``(item id, side)`` per occupied
    slot, matched to rows the same way an open box would be.

    ``candidates`` is consumed once per offer, so it must be a sequence, not an iterator.
    """
    return frozenset().union(
        *(offer_screen_positions(item_id, side, candidates) for item_id, side in live_offers),
        frozenset(),
    )


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
