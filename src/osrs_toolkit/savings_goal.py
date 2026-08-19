"""Grade realized trading profit against a savings goal the player sets for themselves.

Kept free of Qt so it can be tested directly the way ``journal_presentation`` and
``performance`` are. Progress only counts profit realized at or after the goal's own start
time — trading results from before the goal existed were not earned toward it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from osrs_toolkit.journal import TrackedTrade, TradeRecord

DEFAULT_RATE_WINDOW_DAYS = 7


@dataclass(frozen=True, slots=True)
class SavingsProgress:
    label: str
    target: int
    saved: int

    @property
    def remaining(self) -> int:
        return max(0, self.target - self.saved)

    @property
    def percent(self) -> float:
        if self.target <= 0:
            return 100.0 if self.saved > 0 else 0.0
        return max(0.0, min(100.0, self.saved / self.target * 100))

    @property
    def is_reached(self) -> bool:
        return self.target > 0 and self.saved >= self.target


def _instant(raw: str) -> datetime:
    """A stored timestamp, as a comparable instant. A naive value is read as UTC, since
    that is what this app writes (``datetime.now(UTC)``)."""
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _counts_toward_goal(completed_at: str | None, since: datetime) -> bool:
    """Same rule the Trade Journal and Performance page already use for period scoping: a
    position still in progress has no completion time to place relative to ``since``, so it
    stays in scope rather than dropping its already-realized partial profit from the total.
    """
    if completed_at is None:
        return True
    return _instant(completed_at) >= since


def realized_profit_since(
    tracked: list[TrackedTrade], trades: list[TradeRecord], since: datetime
) -> int:
    """Realized profit from positions and manual trades that count toward ``since``.
    Supplies positions are excluded, matching every other results page in the app."""
    total = sum(
        trade.realized_profit
        for trade in tracked
        if trade.status != "Supplies"
        and trade.realized_profit is not None
        and _counts_toward_goal(trade.completed_at, since)
    )
    total += sum(record.profit for record in trades if _instant(record.recorded_at) >= since)
    return total


def daily_profit_rate(
    tracked: list[TrackedTrade],
    trades: list[TradeRecord],
    now: datetime,
    window_days: int = DEFAULT_RATE_WINDOW_DAYS,
) -> float:
    """Average realized profit per day over the trailing window — the rate an ETA is
    projected from, independent of when the goal itself started."""
    since = now - timedelta(days=window_days)
    return realized_profit_since(tracked, trades, since) / window_days


def estimate_days_remaining(remaining: int, daily_rate: float) -> float | None:
    """None when the current rate can never close the gap — a flat or negative trailing
    rate gives no meaningful ETA rather than a misleading one."""
    if remaining <= 0:
        return 0.0
    if daily_rate <= 0:
        return None
    return remaining / daily_rate
