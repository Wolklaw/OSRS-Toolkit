from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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

    @property
    def tooltip(self) -> str:
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

    ``now`` must be timezone-aware; naive stored timestamps are assumed to share its
    timezone (every timestamp this app writes is UTC, via ``datetime.now(UTC)``).
    """
    if period == "All time":
        return True
    if timestamp is None:
        return False
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
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


def journal_status_matches(status: str, selected_filter: str) -> bool:
    """Return whether a journal row belongs in the selected status group."""
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
    raise ValueError(f"Unknown journal status filter: {selected_filter}")


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

    estimated_tone: MoneyTone = (
        "negative" if estimated_profit < 0 else "muted" if estimated_profit > 0 else "neutral"
    )
    return JournalPLPresentation(f"Est. {_signed_gp(estimated_profit)}", estimated_tone)
