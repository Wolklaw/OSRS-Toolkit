from __future__ import annotations

import math
import time

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
    """Return fill-aware passive targets anchored to recent traded averages."""
    buy_price = max(point.low, point.average_low_5m or point.low)
    sell_price = min(point.high, point.average_high_5m or point.high)
    return buy_price, sell_price


def rank_flips(
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    *,
    cash_stack: int = 10_000_000,
    min_roi: float | None = None,
    max_age_seconds: int = 1_800,
    strategy: str = "Balanced (1–4h)",
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
        if item is None or buy_price > cash_stack or sell_price <= buy_price:
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
        position_budget = max(buy_price, int(cash_stack * float(settings["capital_fraction"])))
        affordable = min(cash_stack // buy_price, position_budget // buy_price)
        limit = item.buy_limit or affordable
        projected_volume = round(point.volume_1h * int(settings["hours"]))
        liquid_quantity = max(1, round(point.volume_1h * float(settings["volume_fraction"])))
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


def _stability(point: MarketPoint) -> float:
    if point.average_5m is None or point.average_1h is None or point.average_1h <= 0:
        return 0.35
    drift = abs(point.average_5m - point.average_1h) / point.average_1h
    return max(0.0, 1.0 - min(drift / 0.08, 1.0))
