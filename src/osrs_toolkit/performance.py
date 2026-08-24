"""Grade the trade journal's recorded history against the plans that produced it.

Pure functions, kept free of Qt so they can be tested directly. Every figure is **realized**
— from recorded sale fills, never a current suggestion. Manually entered trades are included
in totals (to match the Trade Journal's summary cards) but excluded from ``calibration``,
since they have no plan to grade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from osrs_toolkit.journal import TrackedTrade, TradeRecord
from osrs_toolkit.journal_presentation import (
    MoneyTone,
    tracked_position_within_period,
    trade_within_period,
)
from osrs_toolkit.ranking import ge_tax

MANUAL_STRATEGY = "Manual entry"

# Ranking a one-off flip beside a hundred of them says nothing useful about the item, so
# the per-item table hides anything below this until the user asks to see everything.
DEFAULT_MINIMUM_ITEM_POSITIONS = 2


@dataclass(frozen=True, slots=True)
class RealizedResult:
    """One finished piece of trading, normalized so every aggregate reads the same shape.

    ``basis`` is the cost of the quantity *actually sold*, not the whole position — a
    half-sold position should only be charged for the capital that produced its result.
    """

    item_name: str
    strategy: str
    profit: int
    basis: int
    hold_hours: float | None

    @property
    def is_win(self) -> bool:
        return self.profit > 0


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    realized_profit: int
    capital_traded: int
    return_on_capital: float | None
    win_rate: float | None
    positions: int
    median_hold_hours: float | None


@dataclass(frozen=True, slots=True)
class GroupPerformance:
    """Aggregated results for one strategy or one item."""

    label: str
    positions: int
    realized_profit: int
    capital_traded: int
    return_on_capital: float | None
    win_rate: float
    median_hold_hours: float | None


@dataclass(frozen=True, slots=True)
class CalibrationRow:
    """One plan-versus-reality comparison, weighted by the quantity behind it."""

    label: str
    planned: int
    actual: int
    positions: int
    favourable_direction: int
    # note: fits in a table column. detail: caveats, shown as the row's tooltip.
    note: str
    detail: str
    signed: bool = False  # Profit can be negative and needs its sign; prices don't.

    @property
    def drift(self) -> float | None:
        if self.planned == 0:
            return None
        return (self.actual - self.planned) / abs(self.planned) * 100

    @property
    def tone(self) -> MoneyTone:
        drift = self.drift
        if drift is None:
            return "neutral"
        # Rounding noise on large weighted sums should not paint a row red.
        if abs(drift) < 0.05:
            return "neutral"
        favourable = (drift > 0) == (self.favourable_direction > 0)
        return "positive" if favourable else "negative"


def _parse_timestamp(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _hold_hours(trade: TrackedTrade) -> float | None:
    """Hours from opening a position to finishing it, where that is known.

    Uses the position's own timestamps, not individual fills — ``update_tracked`` rewrites
    fills wholesale on every edit, which would reset a per-fill timestamp to "now". A
    zero-length hold is treated as unknown rather than a same-instant flip, since positions
    saved before ``completed_at`` existed were backfilled with their creation time.
    """
    started = _parse_timestamp(trade.created_at)
    finished = _parse_timestamp(trade.completed_at)
    if started is None or finished is None:
        return None
    elapsed = (finished - started).total_seconds()
    return elapsed / 3600 if elapsed > 0 else None


def realized_results(
    tracked: list[TrackedTrade],
    trades: list[TradeRecord],
    period: str,
    now: datetime,
) -> list[RealizedResult]:
    """Every position and manual trade that has actually produced a result, in ``period``.

    Period scoping uses the same two helpers as the Trade Journal, so the two pages agree
    on which history is in view.
    """
    results: list[RealizedResult] = []
    for trade in tracked:
        profit = trade.realized_profit
        if profit is None or trade.actual_buy is None:
            continue
        if not tracked_position_within_period(trade.completed_at, period, now):
            continue
        results.append(
            RealizedResult(
                item_name=trade.item_name,
                strategy=trade.strategy,
                profit=profit,
                basis=trade.actual_buy * trade.sold_quantity,
                hold_hours=_hold_hours(trade),
            )
        )
    for record in trades:
        if not trade_within_period(record.recorded_at, period, now):
            continue
        results.append(
            RealizedResult(
                item_name=record.item_name,
                strategy=MANUAL_STRATEGY,
                profit=record.profit,
                basis=record.invested,
                # A manual entry records the outcome, not when the position was opened.
                hold_hours=None,
            )
        )
    return results


def _return_on_capital(results: list[RealizedResult]) -> float | None:
    """Capital-weighted return, not the mean of each position's ROI (a 400gp flip at 60%
    and a 40m flip at 1% shouldn't average to 30.5%)."""
    basis = sum(result.basis for result in results)
    if basis <= 0:
        return None
    return sum(result.profit for result in results) / basis * 100


def _median_hold_hours(results: list[RealizedResult]) -> float | None:
    known = [result.hold_hours for result in results if result.hold_hours is not None]
    return median(known) if known else None


def summarize(results: list[RealizedResult]) -> PerformanceSummary:
    if not results:
        return PerformanceSummary(0, 0, None, None, 0, None)
    return PerformanceSummary(
        realized_profit=sum(result.profit for result in results),
        capital_traded=sum(result.basis for result in results),
        return_on_capital=_return_on_capital(results),
        win_rate=sum(result.is_win for result in results) / len(results) * 100,
        positions=len(results),
        median_hold_hours=_median_hold_hours(results),
    )


def _group(results: list[RealizedResult], label: str) -> GroupPerformance:
    return GroupPerformance(
        label=label,
        positions=len(results),
        realized_profit=sum(result.profit for result in results),
        capital_traded=sum(result.basis for result in results),
        return_on_capital=_return_on_capital(results),
        win_rate=sum(result.is_win for result in results) / len(results) * 100,
        median_hold_hours=_median_hold_hours(results),
    )


def _grouped(
    results: list[RealizedResult], key: str, minimum_positions: int
) -> list[GroupPerformance]:
    buckets: dict[str, list[RealizedResult]] = {}
    for result in results:
        buckets.setdefault(getattr(result, key), []).append(result)
    groups = [
        _group(bucket, label)
        for label, bucket in buckets.items()
        if len(bucket) >= minimum_positions
    ]
    # Most profitable first, and a stable label tiebreak so equal rows do not shuffle
    # between renders of the same data.
    groups.sort(key=lambda group: (-group.realized_profit, group.label))
    return groups


def by_strategy(results: list[RealizedResult]) -> list[GroupPerformance]:
    """One row per strategy the user actually traded under."""
    return _grouped(results, "strategy", minimum_positions=1)


def by_item(
    results: list[RealizedResult],
    minimum_positions: int = DEFAULT_MINIMUM_ITEM_POSITIONS,
) -> list[GroupPerformance]:
    """One row per item, hiding items too rarely traded to say anything about."""
    return _grouped(results, "item_name", minimum_positions=max(1, minimum_positions))


def calibration(tracked: list[TrackedTrade], period: str, now: datetime) -> list[CalibrationRow]:
    """Compare the plan each position was opened with against what it really did.

    Plan is ``target_buy``/``target_sell`` (the original, preserved suggestion), never
    ``current_*_suggestion`` which refreshes against a later market. Prices are weighted by
    quantity, so a 5,000-unit position counts for more than a single-unit one.
    """
    planned_buy = actual_buy = bought = 0
    buy_positions = 0
    planned_sell = actual_sell = sold = 0
    sell_positions = 0
    planned_profit = actual_profit = 0
    profit_positions = 0

    for trade in tracked:
        in_period = tracked_position_within_period(trade.completed_at, period, now)
        if not in_period:
            continue
        # Fills where there are any, else the stored weighted average (same fallback as
        # TrackedTrade.invested) — positions predating per-fill prices only gain fill rows
        # at the next migration, and shouldn't drop out of the buy comparison until then.
        average_buy = trade.average_buy_price if trade.buy_fills else trade.actual_buy
        buy_quantity = trade.bought_quantity if trade.buy_fills else trade.quantity
        if average_buy is not None and trade.target_buy > 0 and buy_quantity > 0:
            planned_buy += trade.target_buy * buy_quantity
            actual_buy += average_buy * buy_quantity
            bought += buy_quantity
            buy_positions += 1
        average_sell = trade.average_sell_price
        if average_sell is not None and trade.target_sell > 0 and trade.sold_quantity > 0:
            planned_sell += trade.target_sell * trade.sold_quantity
            actual_sell += average_sell * trade.sold_quantity
            sold += trade.sold_quantity
            sell_positions += 1
        realized = trade.realized_profit
        if realized is not None and trade.sold_quantity > 0:
            # What the original targets promised, for the quantity that actually sold.
            per_unit = trade.target_sell - trade.target_buy - ge_tax(trade.target_sell)
            planned_profit += per_unit * trade.sold_quantity
            actual_profit += realized
            profit_positions += 1

    rows: list[CalibrationRow] = []
    if bought:
        rows.append(
            CalibrationRow(
                label="Buy price",
                planned=round(planned_buy / bought),
                actual=round(actual_buy / bought),
                positions=buy_positions,
                favourable_direction=-1,
                note="Paying under target is better",
                detail="Weighted by the quantity bought. A position opened straight from a "
                "RuneLite offer takes that offer's own price as its target, so it shows no "
                "drift by definition and pulls this figure toward zero.",
            )
        )
    if sold:
        rows.append(
            CalibrationRow(
                label="Sell price",
                planned=round(planned_sell / sold),
                actual=round(actual_sell / sold),
                positions=sell_positions,
                favourable_direction=1,
                note="Selling above target is better",
                detail="Weighted by the quantity actually sold, so a large position counts "
                "for more than a single-unit one.",
            )
        )
    if profit_positions:
        rows.append(
            CalibrationRow(
                label="Profit after tax",
                planned=planned_profit,
                actual=actual_profit,
                positions=profit_positions,
                favourable_direction=1,
                note="Promise vs. reality, same quantity",
                detail="What the original targets promised for the quantity that actually "
                "sold, against what those sales really made after Grand Exchange tax.",
                signed=True,
            )
        )
    return rows
