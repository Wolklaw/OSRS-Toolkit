from __future__ import annotations

from dataclasses import dataclass

from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax


@dataclass(frozen=True, slots=True)
class AlchCandidate:
    item_id: int
    name: str
    buy_price: int
    alch_value: int
    rune_cost: int
    profit: int
    volume: int


@dataclass(frozen=True, slots=True)
class SkillMethod:
    name: str
    skill: str
    level: int
    actions_hour: int
    input_name: str
    input_count: int
    output_name: str
    output_count: int


@dataclass(frozen=True, slots=True)
class SkillResult:
    name: str
    skill: str
    level: int
    profit_action: int
    profit_hour: int
    eligible: bool


@dataclass(frozen=True, slots=True)
class KingdomResult:
    resource: str
    value_worker: int
    cost_worker: int
    profit_worker: int


SKILL_METHODS = [
    SkillMethod("Cook sharks", "Cooking", 80, 1_300, "Raw shark", 1, "Shark", 1),
    SkillMethod("Cook monkfish", "Cooking", 62, 1_300, "Raw monkfish", 1, "Monkfish", 1),
    SkillMethod("Cook karambwan", "Cooking", 30, 1_300, "Raw karambwan", 1, "Cooked karambwan", 1),
    SkillMethod("Cook anglerfish", "Cooking", 84, 1_300, "Raw anglerfish", 1, "Anglerfish", 1),
    SkillMethod("Cook dark crabs", "Cooking", 90, 1_300, "Raw dark crab", 1, "Dark crab", 1),
    SkillMethod("Spin bow strings", "Crafting", 10, 1_000, "Flax", 1, "Bow string", 1),
    SkillMethod("Smith cannonballs", "Smithing", 35, 600, "Steel bar", 1, "Cannonball", 4),
]

# Expected primary output per worker/day at 100% approval after Royal Trouble.
# Random gems, nests, seeds, caskets, and other secondary loot are deliberately excluded.
KINGDOM_OUTPUTS: dict[str, dict[str, float]] = {
    "Mining (coal)": {"Coal": 54.6},
    "Wood (maple)": {"Maple logs": 89.2},
    "Hardwood (teak)": {"Teak logs": 30.1},
    "Hardwood (mahogany)": {"Mahogany logs": 22.3},
    "Fishing (raw)": {"Raw tuna": 44.0, "Raw swordfish": 13.2},
    "Herbs": {
        "Grimy tarromin": 1.304, "Grimy harralander": 1.174,
        "Grimy irit leaf": 0.782, "Grimy avantoe": 0.782,
        "Grimy ranarr weed": 0.392, "Grimy kwuarm": 0.392,
        "Grimy cadantine": 0.392, "Grimy dwarf weed": 0.392,
        "Grimy lantadyme": 0.392,
    },
}


def price_index(
    mappings: dict[int, ItemMapping], points: list[MarketPoint]
) -> dict[str, MarketPoint]:
    return {
        mappings[point.item_id].name.casefold(): point
        for point in points
        if point.item_id in mappings
    }


def alch_candidates(
    mappings: dict[int, ItemMapping], points: list[MarketPoint]
) -> list[AlchCandidate]:
    prices = price_index(mappings, points)
    nature = prices.get("nature rune")
    if nature is None:
        return []
    results = []
    for item_id, item in mappings.items():
        point = next((p for p in points if p.item_id == item_id), None)
        if point is None or not item.high_alch or point.volume_1h <= 0:
            continue
        profit = item.high_alch - point.high - nature.high
        if profit > 0:
            results.append(
                AlchCandidate(
                    item.item_id,
                    item.name,
                    point.high,
                    item.high_alch,
                    nature.high,
                    profit,
                    point.volume_1h,
                )
            )
    return sorted(results, key=lambda result: result.profit * min(result.volume, 1_200), reverse=True)


def skill_results(
    mappings: dict[int, ItemMapping], points: list[MarketPoint], skills: dict[str, int]
) -> list[SkillResult]:
    prices = price_index(mappings, points)
    results = []
    for method in SKILL_METHODS:
        source, output = prices.get(method.input_name.casefold()), prices.get(method.output_name.casefold())
        if source is None or output is None:
            continue
        input_cost = source.high * method.input_count
        gross_output = output.low * method.output_count
        net_output = gross_output - ge_tax(output.low) * method.output_count
        profit = net_output - input_cost
        results.append(SkillResult(
            method.name, method.skill, method.level, profit,
            profit * method.actions_hour, skills.get(method.skill, 99) >= method.level,
        ))
    return sorted(results, key=lambda result: result.profit_hour, reverse=True)


def kingdom_results(
    mappings: dict[int, ItemMapping], points: list[MarketPoint]
) -> list[KingdomResult]:
    prices = price_index(mappings, points)
    results = []
    for resource, outputs in KINGDOM_OUTPUTS.items():
        value = 0.0
        complete = True
        for item_name, quantity in outputs.items():
            point = prices.get(item_name.casefold())
            if point is None:
                complete = False
                break
            value += (point.low - ge_tax(point.low)) * quantity
        if complete:
            rounded = round(value)
            results.append(KingdomResult(resource, rounded, 5_000, rounded - 5_000))
    return sorted(results, key=lambda result: result.profit_worker, reverse=True)
