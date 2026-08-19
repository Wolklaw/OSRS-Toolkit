from __future__ import annotations

import math
import time
from dataclasses import replace

from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint

GE_TAX_RATE = 0.02
GE_TAX_CAP = 5_000_000

STRATEGIES = {
    "Quick (up to 1h)": {
        "hours": 1,
        "capital_fraction": 0.15,
        "volume_fraction": 0.05,
        "min_roi": 0.3,
        "min_confidence": 45,
        "min_stability": 0.25,
        "adverse_ticks": 1,
        "adverse_rate": 0.001,
        "weights": (0.25, 0.35, 0.10, 0.30),
    },
    "Balanced (1–4h)": {
        "hours": 4,
        "capital_fraction": 0.25,
        "volume_fraction": 0.25,
        "min_roi": 0.5,
        "min_confidence": 55,
        "min_stability": 0.35,
        "adverse_ticks": 2,
        "adverse_rate": 0.0025,
        "weights": (0.35, 0.20, 0.20, 0.25),
    },
    "Overnight (8–12h)": {
        "hours": 8,
        "capital_fraction": 0.20,
        "volume_fraction": 0.50,
        "min_roi": 1.0,
        "min_confidence": 65,
        "min_stability": 0.60,
        "adverse_ticks": 3,
        "adverse_rate": 0.005,
        "weights": (0.30, 0.10, 0.20, 0.40),
    },
}


def ge_tax(sell_price: int) -> int:
    """Return per-item GE tax: 2%, rounded down, capped at 5m."""
    return min(math.floor(max(0, sell_price) * GE_TAX_RATE), GE_TAX_CAP)


def offer_targets(point: MarketPoint) -> tuple[int, int]:
    """Return cautious passive targets anchored across latest, 5m, and 1h trades."""
    buy_observations = (point.low, point.average_low_5m, point.average_low_1h)
    sell_observations = (point.high, point.average_high_5m, point.average_high_1h)
    buy_price = max(price for price in buy_observations if price is not None and price > 0)
    sell_price = min(price for price in sell_observations if price is not None and price > 0)
    return buy_price, sell_price


def rank_flips(
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    *,
    cash_stack: int = 10_000_000,
    min_roi: float | None = None,
    max_age_seconds: int = 1_800,
    strategy: str = "Balanced (1–4h)",
    slot_count: int = 8,
    now: int | None = None,
) -> list[FlipCandidate]:
    """Rank actionable flips without pretending that an observed margin is guaranteed."""
    settings = STRATEGIES.get(strategy, STRATEGIES["Balanced (1–4h)"])
    required_roi = float(settings["min_roi"] if min_roi is None else min_roi)
    current_time = now or int(time.time())
    candidates: list[FlipCandidate] = []
    for point in points:
        item = mappings.get(point.item_id)
        buy_price, sell_price = offer_targets(point)
        if (
            item is None
            or buy_price > cash_stack
            or sell_price <= buy_price
            or not _has_two_sided_liquidity(point)
        ):
            continue
        age = current_time - min(point.high_time, point.low_time)
        tax = ge_tax(sell_price)
        profit = sell_price - buy_price - tax
        roi = profit / buy_price * 100
        if profit <= 0 or roi < required_roi or age < 0 or age > max_age_seconds:
            continue
        adverse_buffer = max(
            int(settings["adverse_ticks"]),
            math.ceil(buy_price * float(settings["adverse_rate"])),
        )
        if profit <= adverse_buffer:
            continue

        freshness = max(0.0, 1.0 - age / max_age_seconds)
        liquidity = min(1.0, math.log1p(point.volume_1h) / math.log1p(20_000))
        stability = _stability(point)
        confidence = round(100 * (0.38 * liquidity + 0.34 * freshness + 0.28 * stability))
        if confidence < int(settings["min_confidence"]) or stability < float(settings["min_stability"]):
            continue
        slot_fraction = 1 / max(1, slot_count)
        capital_fraction = max(float(settings["capital_fraction"]), slot_fraction)
        position_budget = max(buy_price, int(cash_stack * capital_fraction))
        affordable = min(cash_stack // buy_price, position_budget // buy_price)
        limit = item.buy_limit or affordable
        projected_volume = round(point.volume_1h * int(settings["hours"]))
        volume_fraction = min(
            float(settings["hours"]),
            float(settings["volume_fraction"]) * 8 / max(1, slot_count),
        )
        liquid_quantity = max(1, round(point.volume_1h * volume_fraction))
        quantity = min(affordable, limit, liquid_quantity)
        if quantity <= 0:
            continue
        capital_required = buy_price * quantity
        potential = profit * quantity
        # All components are normalized before weighting so confidence is not drowned out by GP.
        profit_quality = min(1.0, math.log1p(potential) / math.log1p(max(cash_stack, 10)))
        roi_quality = min(1.0, roi / 12.0)
        profit_weight, liquidity_weight, roi_weight, confidence_weight = settings["weights"]
        score = 100 * (
            profit_quality * float(profit_weight)
            + liquidity * float(liquidity_weight)
            + roi_quality * float(roi_weight)
            + confidence / 100 * float(confidence_weight)
        )
        candidates.append(
            FlipCandidate(
                item_id=point.item_id, name=item.name, buy_price=buy_price,
                sell_price=sell_price, tax=tax, profit_each=profit, roi=roi,
                hourly_volume=point.volume_1h, projected_volume=projected_volume,
                buy_limit=item.buy_limit, suggested_quantity=quantity,
                capital_required=capital_required,
                potential_profit=potential, confidence=confidence,
                age_seconds=age, score=score,
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def plan_flip_portfolio(
    candidates: list[FlipCandidate],
    cash_stack: int,
    slot_count: int = 8,
) -> list[FlipCandidate]:
    """Allocate one cash stack across distinct GE offers without exceeding safe item caps.

    ``rank_flips`` calculates the maximum sensible size for each item independently. This
    function turns those independent limits into one diversified, cash-constrained plan.
    It tries to use every requested slot, shares spare capital between the selected offers,
    and deliberately leaves cash unused when liquidity, buy limits, or risk caps say to stop.
    """
    if cash_stack <= 0 or slot_count <= 0:
        return []

    eligible = [
        candidate
        for candidate in candidates
        if candidate.suggested_quantity > 0 and candidate.buy_price <= cash_stack
    ]
    selected: list[FlipCandidate] = []
    selected_ids: set[int] = set()
    selected_price_floor = 0
    selected_score = 0.0
    current_utility = -1
    current_capital = -1

    # Forward-select the offer that contributes the most to the complete portfolio at
    # each step. Ranking remains useful for tie-breaking, but a high score can no longer
    # hide a lower-ranked offer with materially more total, confidence-adjusted profit.
    #
    # Every trial re-sizes the whole set, so this runs the sizing pass thousands of times
    # per plan against a full market snapshot. It therefore works in plain numbers and
    # only builds ``FlipCandidate`` objects once, for the set that actually wins.
    for _slot in range(min(slot_count, len(eligible))):
        best_candidate: FlipCandidate | None = None
        best_metric: tuple[int, int, float] | None = None
        for candidate in eligible:
            if candidate.item_id in selected_ids:
                continue
            if selected_price_floor + candidate.buy_price > cash_stack:
                continue
            trial_selected = [*selected, candidate]
            quantities = _size_offers(trial_selected, cash_stack)
            utility = 0
            capital = 0
            for item, quantity in zip(trial_selected, quantities, strict=True):
                utility += item.profit_each * quantity * item.confidence
                capital += item.buy_price * quantity
            metric = (utility, capital, selected_score + candidate.score)
            if best_metric is None or metric > best_metric:
                best_candidate = candidate
                best_metric = metric

        if best_candidate is None or best_metric is None:
            break
        best_utility, best_capital, _score = best_metric
        if best_utility < current_utility or (
            best_utility == current_utility and best_capital < current_capital
        ):
            break
        selected.append(best_candidate)
        selected_ids.add(best_candidate.item_id)
        selected_price_floor += best_candidate.buy_price
        selected_score += best_candidate.score
        current_utility = best_utility
        current_capital = best_capital

    return _allocate_selected(selected, cash_stack)


def _size_offers(selected: list[FlipCandidate], cash_stack: int) -> list[int]:
    """Quantities for a chosen offer set, sized for maximum confidence-adjusted profit.

    One unit of every offer is bought first so the portfolio stays diversified, then the
    spare cash goes to whichever offers return the most confidence-adjusted profit per
    coin committed.
    """
    quantities = [1 for _candidate in selected]
    remaining = cash_stack - sum(candidate.buy_price for candidate in selected)
    priority = sorted(
        range(len(selected)),
        key=lambda index: (
            selected[index].profit_each
            * selected[index].confidence
            / selected[index].buy_price,
            selected[index].score,
        ),
        reverse=True,
    )
    for index in priority:
        candidate = selected[index]
        room = candidate.suggested_quantity - quantities[index]
        units = min(room, remaining // candidate.buy_price)
        quantities[index] += units
        remaining -= units * candidate.buy_price
    return quantities


def _allocate_selected(
    selected: list[FlipCandidate], cash_stack: int
) -> list[FlipCandidate]:
    """``_size_offers`` applied to the offers themselves, for the plan that is returned."""
    return [
        replace(
            candidate,
            suggested_quantity=quantity,
            capital_required=candidate.buy_price * quantity,
            potential_profit=candidate.profit_each * quantity,
        )
        for candidate, quantity in zip(
            selected, _size_offers(selected, cash_stack), strict=True
        )
    ]


def _stability(point: MarketPoint) -> float:
    window_pairs = (
        (point.average_high_5m, point.average_high_1h),
        (point.average_low_5m, point.average_low_1h),
    )
    drifts = [
        abs(recent - hourly) / hourly
        for recent, hourly in window_pairs
        if recent is not None and hourly is not None and hourly > 0
    ]
    if not drifts:
        return 0.35
    drift = max(drifts)
    return max(0.0, 1.0 - min(drift / 0.08, 1.0))


def _has_two_sided_liquidity(point: MarketPoint) -> bool:
    """Reject spreads supported by only a token trade on one side of the market."""
    total = point.high_volume_1h + point.low_volume_1h
    if total <= 0:
        # Keeps manually constructed/legacy snapshots usable. Live snapshots always
        # include the separate high- and low-price volumes.
        return True
    thinner_side = min(point.high_volume_1h, point.low_volume_1h)
    return thinner_side >= 3 and thinner_side / total >= 0.02
