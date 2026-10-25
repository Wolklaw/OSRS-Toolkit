from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import replace

from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint

GE_TAX_RATE = 0.02
GE_TAX_CAP = 5_000_000

TAX_EXEMPT_ITEMS: tuple[tuple[int, str], ...] = (
    (13190, "Old school bond"),
    (365, "Bass"),
    (2309, "Bread"),
    (1891, "Cake"),
    (2140, "Cooked chicken"),
    (2142, "Cooked meat"),
    (347, "Herring"),
    (379, "Lobster"),
    (355, "Mackerel"),
    (2327, "Meat pie"),
    (351, "Pike"),
    (329, "Salmon"),
    (315, "Shrimps"),
    (361, "Tuna"),
    (882, "Bronze arrow"),
    (806, "Bronze dart"),
    (884, "Iron arrow"),
    (807, "Iron dart"),
    (558, "Mind rune"),
    (886, "Steel arrow"),
    (808, "Steel dart"),
    (8011, "Ardougne teleport (tablet)"),
    (8010, "Camelot teleport (tablet)"),
    (28824, "Civitas illa fortis teleport"),
    (8009, "Falador teleport (tablet)"),
    (3853, "Games necklace(8)"),
    (28790, "Kourend castle teleport (tablet)"),
    (8008, "Lumbridge teleport (tablet)"),
    (2552, "Ring of dueling(8)"),
    (8013, "Teleport to house (tablet)"),
    (8007, "Varrock teleport (tablet)"),
    (1755, "Chisel"),
    (5325, "Gardening trowel"),
    (1785, "Glassblowing pipe"),
    (2347, "Hammer"),
    (1733, "Needle"),
    (233, "Pestle and mortar"),
    (5341, "Rake"),
    (8794, "Saw"),
    (5329, "Secateurs"),
    (5343, "Seed dibber"),
    (1735, "Shears"),
    (952, "Spade"),
    (5331, "Watering can"),
)

TAX_EXEMPT_ITEM_IDS = frozenset(item_id for item_id, _name in TAX_EXEMPT_ITEMS)
_TAX_EXEMPT_ITEM_NAMES = frozenset(name.casefold() for _item_id, name in TAX_EXEMPT_ITEMS)


def is_tax_exempt(item_id: int | None = None, item_name: str | None = None) -> bool:
    if item_id is not None and item_id in TAX_EXEMPT_ITEM_IDS:
        return True
    return item_name is not None and item_name.strip().casefold() in _TAX_EXEMPT_ITEM_NAMES


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


def confidence_standing(confidence: int, floor: int) -> float:
    span = max(1, 100 - floor)
    return max(0.0, min(1.0, (confidence - floor) / span))


MAX_CASH_STACK = 2_147_483_647


def ge_tax(sell_price: int, *, exempt: bool = False) -> int:
    if exempt:
        return 0
    return min(math.floor(max(0, sell_price) * GE_TAX_RATE), GE_TAX_CAP)


def offer_targets(point: MarketPoint) -> tuple[int, int]:
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
    remaining_limits: Mapping[int, int] | None = None,
    now: int | None = None,
    include_all: bool = False,
) -> list[FlipCandidate]:
    settings = STRATEGIES.get(strategy, STRATEGIES["Balanced (1–4h)"])
    required_roi = float(settings["min_roi"] if min_roi is None else min_roi)
    current_time = now or int(time.time())
    candidates: list[FlipCandidate] = []
    for point in points:
        item = mappings.get(point.item_id)
        buy_price, sell_price = offer_targets(point)
        if item is None or buy_price > cash_stack or sell_price <= buy_price:
            continue
        meets_strategy = _has_two_sided_liquidity(point)
        if not meets_strategy and not include_all:
            continue
        age = current_time - min(point.high_time, point.low_time)
        tax = ge_tax(sell_price, exempt=is_tax_exempt(point.item_id, item.name))
        profit = sell_price - buy_price - tax
        roi = profit / buy_price * 100
        if profit <= 0:
            continue
        if roi < required_roi or age < 0 or age > max_age_seconds:
            meets_strategy = False
            if not include_all:
                continue
        adverse_buffer = max(
            int(settings["adverse_ticks"]),
            math.ceil(buy_price * float(settings["adverse_rate"])),
        )
        if profit <= adverse_buffer:
            meets_strategy = False
            if not include_all:
                continue

        freshness = max(0.0, 1.0 - age / max_age_seconds)
        liquidity = min(1.0, math.log1p(point.volume_1h) / math.log1p(20_000))
        stability = _stability(point)
        confidence = round(100 * (0.38 * liquidity + 0.34 * freshness + 0.28 * stability))
        if confidence < int(settings["min_confidence"]) or stability < float(
            settings["min_stability"]
        ):
            meets_strategy = False
            if not include_all:
                continue
        slot_fraction = 1 / max(1, slot_count)
        capital_fraction = max(float(settings["capital_fraction"]), slot_fraction)
        position_budget = max(buy_price, int(cash_stack * capital_fraction))
        affordable = min(cash_stack // buy_price, position_budget // buy_price)
        limit = item.buy_limit or affordable
        if remaining_limits is not None:
            room = remaining_limits.get(point.item_id)
            if room is not None:
                limit = min(limit, room)
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
                item_id=point.item_id,
                name=item.name,
                buy_price=buy_price,
                sell_price=sell_price,
                tax=tax,
                profit_each=profit,
                roi=roi,
                hourly_volume=point.volume_1h,
                projected_volume=projected_volume,
                buy_limit=item.buy_limit,
                suggested_quantity=quantity,
                capital_required=capital_required,
                potential_profit=potential,
                confidence=confidence,
                age_seconds=age,
                score=score,
                meets_strategy=meets_strategy,
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def plan_flip_portfolio(
    candidates: list[FlipCandidate],
    cash_stack: int,
    slot_count: int = 8,
) -> list[FlipCandidate]:
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
    quantities = [1 for _candidate in selected]
    remaining = cash_stack - sum(candidate.buy_price for candidate in selected)
    priority = sorted(
        range(len(selected)),
        key=lambda index: (
            selected[index].profit_each * selected[index].confidence / selected[index].buy_price,
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


def _allocate_selected(selected: list[FlipCandidate], cash_stack: int) -> list[FlipCandidate]:
    return [
        replace(
            candidate,
            suggested_quantity=quantity,
            capital_required=candidate.buy_price * quantity,
            potential_profit=candidate.profit_each * quantity,
        )
        for candidate, quantity in zip(selected, _size_offers(selected, cash_stack), strict=True)
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
    total = point.high_volume_1h + point.low_volume_1h
    if total <= 0:
        return True
    thinner_side = min(point.high_volume_1h, point.low_volume_1h)
    return thinner_side >= 3 and thinner_side / total >= 0.02
