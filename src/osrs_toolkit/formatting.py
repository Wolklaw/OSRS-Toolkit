"""Turning numbers into the strings a person reads.

Everything here is presentation, and nothing here knows what is drawing it. It was written
inside the desktop window, which was fine while that window was the only thing displaying any
of it. A second front end is the difference between a helper and a duplicated helper — one of
these had already been copied into ``journal_presentation`` before this module existed.

The choices carry judgement, which is why they live together rather than being inlined at each
call site: whether a tiny sliver of progress reads as "0%" or "<1%" is a decision about what
the reader should conclude from it, and two screens should not be able to answer it
differently.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported for annotations only. ``performance`` imports ``journal_presentation``, which
    # imports this module, so pulling either in at runtime would close the loop. Annotations
    # are strings under ``from __future__ import annotations``, so none of them are needed
    # until something asks for the types.
    from osrs_toolkit.journal import SyncedItem, SyncedTrade
    from osrs_toolkit.performance import GroupPerformance


def gp(value: int) -> str:
    return f"{value:,} gp"


def signed_gp(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,} gp"


def percent(value: float | None, *, signed: bool = False) -> str:
    """A percentage that never rounds a real result away to a bare "-0.0%"."""
    if value is None:
        return "—"
    places = 2 if value and abs(value) < 0.05 else 1
    return f"{value:+.{places}f}%" if signed else f"{value:.{places}f}%"


def hold_time(hours: float | None) -> str:
    """A duration at a readable scale: minutes for quick flips, days for overnight ones."""
    if hours is None:
        return "—"
    if hours < 1:
        return f"{round(hours * 60):,} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def availability(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Yes" if value else "No"


def short_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} sec"
    if seconds < 3_600:
        return f"{seconds // 60} min"
    return f"{seconds // 3_600} hr"


def format_countdown(seconds: int) -> str:
    """Hours-and-minutes countdown for the buy-limit "resets in" column, precise enough to
    be useful against a 4-hour window without needing seconds."""
    if seconds <= 0:
        return "any moment"
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_goal_percent(percent: float) -> str:
    """Rounding a real but tiny sliver of progress straight to "0%" reads as no progress
    at all against a large target — distinct from an actual 0%."""
    if 0 < percent < 1:
        return "<1%"
    return f"{percent:.0f}%"


def format_eta(days: float) -> str:
    """A savings goal's ETA is only ever a rough projection, so it's shown in whatever
    unit keeps it readable — "~19,671 days" doesn't parse at a glance the way "~54 years"
    does, and small values stay in days where that's still the natural unit."""
    if days < 1:
        return "less than a day"
    if days < 365:
        return f"~{days:.0f} days"
    years = days / 365
    return f"~{years:.0f} years" if years >= 10 else f"~{years:.1f} years"


def display_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def item_detail_lines(items: tuple[SyncedItem, ...]) -> list[str]:
    if not items:
        return ["  Nothing"]
    lines: list[str] = []
    for item in items:
        if item.item_id == 995:
            lines.append(f"  {gp(item.quantity)}")
        else:
            lines.append(
                f"  {item.quantity:,} × {item.item_name} "
                f"(estimated {gp(item.total_value)})"
            )
    return lines


def compact_items(items: tuple[SyncedItem, ...]) -> str:
    labels = [
        gp(item.quantity)
        if item.item_id == 995
        else f"{item.quantity:,} × {item.item_name}"
        for item in items
    ]
    if len(labels) <= 2:
        return ", ".join(labels) if labels else "Nothing"
    return f"{', '.join(labels[:2])} +{len(labels) - 2} more"


def synced_trade_label(trade: SyncedTrade) -> str:
    if trade.event_type == "player_trade":
        return f"With {trade.counterparty}" if trade.counterparty else "Player trade"
    items = trade.received if trade.direction == "buy" else trade.given
    item = next((entry for entry in items if entry.item_id != 995), None)
    action = "Bought" if trade.direction == "buy" else "Sold"
    return f"{action} {item.item_name}" if item else action


def group_row(group: GroupPerformance, *, hold: bool) -> list[str]:
    row = [
        group.label,
        f"{group.positions:,}",
        percent(group.win_rate),
        signed_gp(group.realized_profit),
        percent(group.return_on_capital),
        gp(group.capital_traded),
    ]
    if hold:
        row.append(hold_time(group.median_hold_hours))
    return row


def attention_tooltip(asking: int, live_sell_price: int) -> str:
    """Why a journal row is flagged, in lines short enough to read at a glance.

    Broken across three of them deliberately. Qt renders a plain-text tooltip on a single
    line however long it is, and the one sentence this used to be stretched most of the
    window — laid over the rows underneath it, which is exactly where the eye was looking.
    """
    drop_pct = (asking - live_sell_price) / asking * 100
    return (
        "This ask looks stale.\n"
        f"Asking {gp(asking)} · market now suggests {gp(live_sell_price)}"
        f" ({drop_pct:.1f}% lower).\n"
        "Unlikely to fill here — relist nearer the suggestion."
    )
