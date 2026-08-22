from __future__ import annotations

import time
from dataclasses import dataclass

from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax


@dataclass(frozen=True, slots=True)
class AlchCandidate:
    item_id: int
    name: str
    latest_buy_price: int
    buy_price: int
    alch_value: int
    rune_cost: int
    profit: int
    volume: int
    buy_limit: int
    safe_quantity: int
    capital_required: int
    hourly_profit: int
    roi: float
    age_seconds: int
    eligible: bool | None


@dataclass(frozen=True, slots=True)
class AlchPolicy:
    min_volume: int
    max_age_seconds: int | None
    volume_share: float


ALCH_POLICIES: dict[str, AlchPolicy] = {
    "Safer": AlchPolicy(min_volume=200, max_age_seconds=600, volume_share=0.05),
    "Balanced": AlchPolicy(min_volume=50, max_age_seconds=1_800, volume_share=0.10),
    "Show all": AlchPolicy(min_volume=0, max_age_seconds=None, volume_share=1.0),
}

SKILL_MAX_PRICE_AGE_SECONDS = 21_600


@dataclass(frozen=True, slots=True)
class SkillItem:
    item_id: int
    name: str
    quantity: float = 1.0


@dataclass(frozen=True, slots=True)
class SkillMethod:
    name: str
    skill: str
    level: int
    actions_hour: int
    inputs: tuple[SkillItem, ...]
    outputs: tuple[SkillItem, ...]
    fixed_cost: int = 0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class SkillResult:
    name: str
    skill: str
    level: int
    input_cost: int
    output_value: int
    profit_action: int
    actions_hour: int
    profit_hour: int
    eligible: bool | None
    price_age_seconds: int
    notes: str
    guide_url: str


def _item(item_id: int, name: str, quantity: float = 1.0) -> SkillItem:
    return SkillItem(item_id, name, quantity)


SKILL_GUIDES = {
    "Cooking": "https://oldschool.runescape.wiki/w/Pay-to-play_Cooking_training",
    "Crafting": "https://oldschool.runescape.wiki/w/Pay-to-play_Crafting_training",
    "Fletching": "https://oldschool.runescape.wiki/w/Fletching_training",
    "Smithing": "https://oldschool.runescape.wiki/w/Pay-to-play_Smithing_training",
    "Herblore": "https://oldschool.runescape.wiki/w/Herblore_training",
    "Magic": "https://oldschool.runescape.wiki/w/Pay-to-play_Magic_training",
    "Mining": "https://oldschool.runescape.wiki/w/Pay-to-play_Mining_training",
    "Woodcutting": "https://oldschool.runescape.wiki/w/Pay-to-play_Woodcutting_training",
    "Fishing": "https://oldschool.runescape.wiki/w/Pay-to-play_Fishing_training",
    "Hunter": "https://oldschool.runescape.wiki/w/Hunter_training",
}


SKILL_METHODS = [
    # Cooking. Profit assumes every raw fish cooks successfully; the UI calls this out explicitly.
    SkillMethod(
        "Cook trout",
        "Cooking",
        15,
        1_300,
        (_item(335, "Raw trout"),),
        (_item(333, "Trout"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook salmon",
        "Cooking",
        25,
        1_300,
        (_item(331, "Raw salmon"),),
        (_item(329, "Salmon"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook tuna",
        "Cooking",
        30,
        1_300,
        (_item(359, "Raw tuna"),),
        (_item(361, "Tuna"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook lobster",
        "Cooking",
        40,
        1_300,
        (_item(377, "Raw lobster"),),
        (_item(379, "Lobster"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook swordfish",
        "Cooking",
        45,
        1_300,
        (_item(371, "Raw swordfish"),),
        (_item(373, "Swordfish"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook monkfish",
        "Cooking",
        62,
        1_300,
        (_item(7944, "Raw monkfish"),),
        (_item(7946, "Monkfish"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook shark",
        "Cooking",
        80,
        1_300,
        (_item(383, "Raw shark"),),
        (_item(385, "Shark"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook anglerfish",
        "Cooking",
        84,
        1_300,
        (_item(13439, "Raw anglerfish"),),
        (_item(13441, "Anglerfish"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook dark crab",
        "Cooking",
        90,
        1_300,
        (_item(11934, "Raw dark crab"),),
        (_item(11936, "Dark crab"),),
        notes="Assumes no burns",
    ),
    SkillMethod(
        "Cook karambwan",
        "Cooking",
        30,
        1_300,
        (_item(3142, "Raw karambwan"),),
        (_item(3144, "Cooked karambwan"),),
        notes="Assumes no burns",
    ),
    # Crafting and item processing.
    SkillMethod(
        "Spin bow strings",
        "Crafting",
        10,
        1_000,
        (_item(1779, "Flax"),),
        (_item(1777, "Bow string"),),
    ),
    SkillMethod(
        "Cut sapphires",
        "Crafting",
        20,
        2_500,
        (_item(1623, "Uncut sapphire"),),
        (_item(1607, "Sapphire"),),
    ),
    SkillMethod(
        "Cut emeralds",
        "Crafting",
        27,
        2_500,
        (_item(1621, "Uncut emerald"),),
        (_item(1605, "Emerald"),),
    ),
    SkillMethod(
        "Cut rubies", "Crafting", 34, 2_500, (_item(1619, "Uncut ruby"),), (_item(1603, "Ruby"),)
    ),
    SkillMethod(
        "Cut diamonds",
        "Crafting",
        43,
        2_500,
        (_item(1617, "Uncut diamond"),),
        (_item(1601, "Diamond"),),
    ),
    SkillMethod(
        "Craft sapphire bracelets",
        "Crafting",
        23,
        1_100,
        (_item(2357, "Gold bar"), _item(1607, "Sapphire")),
        (_item(11072, "Sapphire bracelet"),),
    ),
    SkillMethod(
        "Craft emerald bracelets",
        "Crafting",
        30,
        1_100,
        (_item(2357, "Gold bar"), _item(1605, "Emerald")),
        (_item(11076, "Emerald bracelet"),),
    ),
    SkillMethod(
        "Craft ruby bracelets",
        "Crafting",
        42,
        1_100,
        (_item(2357, "Gold bar"), _item(1603, "Ruby")),
        (_item(11085, "Ruby bracelet"),),
    ),
    SkillMethod(
        "Craft diamond bracelets",
        "Crafting",
        58,
        1_100,
        (_item(2357, "Gold bar"), _item(1601, "Diamond")),
        (_item(11092, "Diamond bracelet"),),
    ),
    SkillMethod(
        "Attach air orbs",
        "Crafting",
        66,
        2_400,
        (_item(1391, "Battlestaff"), _item(573, "Air orb")),
        (_item(1397, "Air battlestaff"),),
    ),
    SkillMethod(
        "Attach water orbs",
        "Crafting",
        54,
        2_400,
        (_item(1391, "Battlestaff"), _item(571, "Water orb")),
        (_item(1395, "Water battlestaff"),),
    ),
    SkillMethod(
        "Attach earth orbs",
        "Crafting",
        58,
        2_400,
        (_item(1391, "Battlestaff"), _item(575, "Earth orb")),
        (_item(1399, "Earth battlestaff"),),
    ),
    SkillMethod(
        "Attach fire orbs",
        "Crafting",
        62,
        2_400,
        (_item(1391, "Battlestaff"), _item(569, "Fire orb")),
        (_item(1393, "Fire battlestaff"),),
    ),
    SkillMethod(
        "Tan soft leather",
        "Crafting",
        1,
        2_800,
        (_item(1739, "Cowhide"),),
        (_item(1741, "Leather"),),
        fixed_cost=1,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Tan hard leather",
        "Crafting",
        1,
        2_800,
        (_item(1739, "Cowhide"),),
        (_item(1743, "Hard leather"),),
        fixed_cost=3,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Tan green dragonhide",
        "Crafting",
        1,
        2_800,
        (_item(1753, "Green dragonhide"),),
        (_item(1745, "Green dragon leather"),),
        fixed_cost=20,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Tan blue dragonhide",
        "Crafting",
        1,
        2_800,
        (_item(1751, "Blue dragonhide"),),
        (_item(2505, "Blue dragon leather"),),
        fixed_cost=20,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Tan red dragonhide",
        "Crafting",
        1,
        2_800,
        (_item(1749, "Red dragonhide"),),
        (_item(2507, "Red dragon leather"),),
        fixed_cost=20,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Tan black dragonhide",
        "Crafting",
        1,
        2_800,
        (_item(1747, "Black dragonhide"),),
        (_item(2509, "Black dragon leather"),),
        fixed_cost=20,
        notes="Includes tannery fee",
    ),
    SkillMethod(
        "Craft drift nets",
        "Crafting",
        26,
        900,
        (_item(5931, "Jute fibre", 2),),
        (_item(21652, "Drift net"),),
    ),
    SkillMethod(
        "Blow unpowered orbs",
        "Crafting",
        46,
        1_600,
        (_item(1775, "Molten glass"),),
        (_item(567, "Unpowered orb"),),
    ),
    # Fletching.
    SkillMethod(
        "Fletch headless arrows",
        "Fletching",
        1,
        2_700,
        (_item(52, "Arrow shaft", 15), _item(314, "Feather", 15)),
        (_item(53, "Headless arrow", 15),),
        notes="15 arrows per action",
    ),
    SkillMethod(
        "String maple longbows",
        "Fletching",
        55,
        1_500,
        (_item(62, "Maple longbow (u)"), _item(1777, "Bow string")),
        (_item(851, "Maple longbow"),),
    ),
    SkillMethod(
        "String yew longbows",
        "Fletching",
        70,
        1_500,
        (_item(66, "Yew longbow (u)"), _item(1777, "Bow string")),
        (_item(855, "Yew longbow"),),
    ),
    SkillMethod(
        "String magic longbows",
        "Fletching",
        85,
        1_500,
        (_item(70, "Magic longbow (u)"), _item(1777, "Bow string")),
        (_item(859, "Magic longbow"),),
    ),
    SkillMethod(
        "Cut diamond bolt tips",
        "Fletching",
        65,
        1_300,
        (_item(1601, "Diamond"),),
        (_item(9192, "Diamond bolt tips", 12),),
    ),
    # Smithing. Item IDs avoid the Cannonball -> Steel cannonball rename that hid this row.
    SkillMethod(
        "Smith steel cannonballs",
        "Smithing",
        35,
        600,
        (_item(2353, "Steel bar"),),
        (_item(2, "Steel cannonball", 4),),
    ),
    SkillMethod(
        "Smith steel cannonballs (double mould)",
        "Smithing",
        35,
        600,
        (_item(2353, "Steel bar", 2),),
        (_item(2, "Steel cannonball", 8),),
        notes="Requires double ammo mould",
    ),
    SkillMethod(
        "Smith bronze dart tips",
        "Smithing",
        4,
        1_200,
        (_item(2349, "Bronze bar"),),
        (_item(819, "Bronze dart tip", 10),),
    ),
    SkillMethod(
        "Smith iron dart tips",
        "Smithing",
        19,
        1_200,
        (_item(2351, "Iron bar"),),
        (_item(820, "Iron dart tip", 10),),
    ),
    SkillMethod(
        "Smith steel dart tips",
        "Smithing",
        34,
        1_200,
        (_item(2353, "Steel bar"),),
        (_item(821, "Steel dart tip", 10),),
    ),
    SkillMethod(
        "Smith mithril dart tips",
        "Smithing",
        54,
        1_200,
        (_item(2359, "Mithril bar"),),
        (_item(822, "Mithril dart tip", 10),),
    ),
    SkillMethod(
        "Smith adamant dart tips",
        "Smithing",
        74,
        1_200,
        (_item(2361, "Adamantite bar"),),
        (_item(823, "Adamant dart tip", 10),),
    ),
    SkillMethod(
        "Smith rune dart tips",
        "Smithing",
        89,
        1_200,
        (_item(2363, "Runite bar"),),
        (_item(824, "Rune dart tip", 10),),
    ),
    # Herblore.
    SkillMethod(
        "Clean ranarr weed",
        "Herblore",
        25,
        5_000,
        (_item(207, "Grimy ranarr weed"),),
        (_item(257, "Ranarr weed"),),
    ),
    SkillMethod(
        "Clean avantoe",
        "Herblore",
        48,
        5_000,
        (_item(211, "Grimy avantoe"),),
        (_item(261, "Avantoe"),),
    ),
    SkillMethod(
        "Clean kwuarm",
        "Herblore",
        54,
        5_000,
        (_item(213, "Grimy kwuarm"),),
        (_item(263, "Kwuarm"),),
    ),
    SkillMethod(
        "Clean snapdragon",
        "Herblore",
        59,
        5_000,
        (_item(3051, "Grimy snapdragon"),),
        (_item(3000, "Snapdragon"),),
    ),
    SkillMethod(
        "Clean lantadyme",
        "Herblore",
        67,
        5_000,
        (_item(2485, "Grimy lantadyme"),),
        (_item(2481, "Lantadyme"),),
    ),
    SkillMethod(
        "Clean dwarf weed",
        "Herblore",
        70,
        5_000,
        (_item(217, "Grimy dwarf weed"),),
        (_item(267, "Dwarf weed"),),
    ),
    SkillMethod(
        "Clean torstol",
        "Herblore",
        75,
        5_000,
        (_item(219, "Grimy torstol"),),
        (_item(269, "Torstol"),),
    ),
    SkillMethod(
        "Make prayer potions",
        "Herblore",
        38,
        2_500,
        (_item(99, "Ranarr potion (unf)"), _item(231, "Snape grass")),
        (_item(139, "Prayer potion(3)"),),
    ),
    SkillMethod(
        "Make super energy potions",
        "Herblore",
        52,
        2_500,
        (_item(103, "Avantoe potion (unf)"), _item(2970, "Mort myre fungus")),
        (_item(3018, "Super energy(3)"),),
    ),
    SkillMethod(
        "Make super strength potions",
        "Herblore",
        55,
        2_500,
        (_item(105, "Kwuarm potion (unf)"), _item(225, "Limpwurt root")),
        (_item(157, "Super strength(3)"),),
    ),
    SkillMethod(
        "Make super restore potions",
        "Herblore",
        63,
        2_500,
        (_item(3004, "Snapdragon potion (unf)"), _item(223, "Red spiders' eggs")),
        (_item(3026, "Super restore(3)"),),
    ),
    SkillMethod(
        "Make antifire potions",
        "Herblore",
        69,
        2_500,
        (_item(2483, "Lantadyme potion (unf)"), _item(241, "Dragon scale dust")),
        (_item(2454, "Antifire potion(3)"),),
    ),
    SkillMethod(
        "Make ranging potions",
        "Herblore",
        72,
        2_500,
        (_item(109, "Dwarf weed potion (unf)"), _item(245, "Wine of Zamorak")),
        (_item(169, "Ranging potion(3)"),),
    ),
    SkillMethod(
        "Make Saradomin brews",
        "Herblore",
        81,
        2_500,
        (_item(3002, "Toadflax potion (unf)"), _item(6693, "Crushed nest")),
        (_item(6687, "Saradomin brew(3)"),),
    ),
    # Magic jewellery enchants. Elemental-rune costs assume the appropriate elemental staff.
    SkillMethod(
        "Enchant sapphire rings",
        "Magic",
        7,
        1_800,
        (_item(1637, "Sapphire ring"), _item(564, "Cosmic rune")),
        (_item(2550, "Ring of recoil"),),
        notes="Assumes elemental staff",
    ),
    SkillMethod(
        "Enchant sapphire necklaces",
        "Magic",
        7,
        1_800,
        (_item(1656, "Sapphire necklace"), _item(564, "Cosmic rune")),
        (_item(3853, "Games necklace(8)"),),
        notes="Assumes elemental staff",
    ),
    SkillMethod(
        "Enchant emerald rings",
        "Magic",
        27,
        1_800,
        (_item(1639, "Emerald ring"), _item(564, "Cosmic rune")),
        (_item(2552, "Ring of dueling(8)"),),
        notes="Assumes elemental staff",
    ),
    SkillMethod(
        "Enchant diamond amulets",
        "Magic",
        57,
        1_800,
        (_item(1700, "Diamond amulet"), _item(564, "Cosmic rune")),
        (_item(1731, "Amulet of power"),),
        notes="Assumes elemental staff",
    ),
    # Gathering activities. Rates are conservative baselines and vary with route, gear, and focus.
    SkillMethod(
        "Mine iron ore",
        "Mining",
        15,
        1_000,
        (),
        (_item(440, "Iron ore"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Mine coal",
        "Mining",
        30,
        400,
        (),
        (_item(453, "Coal"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Mine mithril ore",
        "Mining",
        55,
        100,
        (),
        (_item(447, "Mithril ore"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Mine adamantite ore",
        "Mining",
        70,
        60,
        (),
        (_item(449, "Adamantite ore"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Mine runite ore",
        "Mining",
        85,
        10,
        (),
        (_item(451, "Runite ore"),),
        notes="Baseline; competition dependent",
    ),
    SkillMethod(
        "Mine amethyst",
        "Mining",
        92,
        80,
        (),
        (_item(21347, "Amethyst"),),
        notes="Baseline; gear dependent",
    ),
    SkillMethod(
        "Chop oak logs",
        "Woodcutting",
        15,
        600,
        (),
        (_item(1521, "Oak logs"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Chop willow logs",
        "Woodcutting",
        30,
        700,
        (),
        (_item(1519, "Willow logs"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Chop maple logs",
        "Woodcutting",
        45,
        400,
        (),
        (_item(1517, "Maple logs"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Chop yew logs",
        "Woodcutting",
        60,
        180,
        (),
        (_item(1515, "Yew logs"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Chop magic logs",
        "Woodcutting",
        75,
        100,
        (),
        (_item(1513, "Magic logs"),),
        notes="Baseline; route and gear dependent",
    ),
    SkillMethod(
        "Chop redwood logs",
        "Woodcutting",
        90,
        150,
        (),
        (_item(19669, "Redwood logs"),),
        notes="Baseline; gear dependent",
    ),
    SkillMethod(
        "Fish lobsters",
        "Fishing",
        40,
        250,
        (),
        (_item(377, "Raw lobster"),),
        notes="Baseline; location and gear dependent",
    ),
    SkillMethod(
        "Fish monkfish",
        "Fishing",
        62,
        300,
        (),
        (_item(7944, "Raw monkfish"),),
        notes="Baseline; location and gear dependent",
    ),
    SkillMethod(
        "Fish karambwan",
        "Fishing",
        65,
        600,
        (),
        (_item(3142, "Raw karambwan"),),
        notes="Baseline; banking route dependent",
    ),
    SkillMethod(
        "Fish sharks",
        "Fishing",
        76,
        150,
        (),
        (_item(383, "Raw shark"),),
        notes="Baseline; location and gear dependent",
    ),
    SkillMethod(
        "Fish anglerfish",
        "Fishing",
        82,
        120,
        (),
        (_item(13439, "Raw anglerfish"),),
        notes="Baseline; location and gear dependent",
    ),
    SkillMethod(
        "Fish dark crabs",
        "Fishing",
        85,
        350,
        (),
        (_item(11934, "Raw dark crab"),),
        notes="Wilderness risk; gear dependent",
    ),
    SkillMethod(
        "Hunt chinchompas",
        "Hunter",
        53,
        220,
        (),
        (_item(10033, "Chinchompa"),),
        notes="Baseline; location and focus dependent",
    ),
    SkillMethod(
        "Hunt red chinchompas",
        "Hunter",
        63,
        300,
        (),
        (_item(10034, "Red chinchompa"),),
        notes="Baseline; location and focus dependent",
    ),
    SkillMethod(
        "Hunt black chinchompas",
        "Hunter",
        73,
        350,
        (),
        (_item(11959, "Black chinchompa"),),
        notes="Wilderness risk; focus dependent",
    ),
]


def conservative_buy_price(point: MarketPoint) -> int:
    """Use the highest recent buyer-paid observation to avoid optimistic input costs."""
    observations = [point.high, point.average_high_5m, point.average_high_1h]
    return max(value for value in observations if value is not None and value > 0)


def conservative_sell_price(point: MarketPoint) -> int:
    """Use the lowest recent seller-received observation for cautious output values."""
    observations = [point.low, point.average_low_5m, point.average_low_1h]
    return min(value for value in observations if value is not None and value > 0)


def alch_candidates(
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    *,
    cash_stack: int = 10_000_000,
    policy: str = "Safer",
    magic_level: int | None = None,
    now: int | None = None,
) -> list[AlchCandidate]:
    point_by_id = {point.item_id: point for point in points}
    nature = point_by_id.get(561)
    if nature is None:
        return []
    selected_policy = ALCH_POLICIES.get(policy, ALCH_POLICIES["Safer"])
    current_time = int(time.time()) if now is None else now
    rune_cost = conservative_buy_price(nature)
    results = []
    for item_id, item in mappings.items():
        point = point_by_id.get(item_id)
        if point is None or not item.high_alch or item.buy_limit is None:
            continue
        age = current_time - point.high_time
        if age < 0 or point.volume_1h < selected_policy.min_volume:
            continue
        if selected_policy.max_age_seconds is not None and age > selected_policy.max_age_seconds:
            continue
        buy_price = conservative_buy_price(point)
        profit = item.high_alch - buy_price - rune_cost
        if profit <= 0:
            continue
        cost_each = buy_price + rune_cost
        cash_cap = cash_stack // cost_each if cost_each else 0
        volume_cap = int(point.volume_1h * selected_policy.volume_share)
        if selected_policy.volume_share >= 1:
            volume_cap = point.volume_1h
        safe_quantity = min(1_200, item.buy_limit, cash_cap, volume_cap)
        if safe_quantity <= 0:
            continue
        capital_required = cost_each * safe_quantity
        results.append(
            AlchCandidate(
                item_id=item.item_id,
                name=item.name,
                latest_buy_price=point.high,
                buy_price=buy_price,
                alch_value=item.high_alch,
                rune_cost=rune_cost,
                profit=profit,
                volume=point.volume_1h,
                buy_limit=item.buy_limit,
                safe_quantity=safe_quantity,
                capital_required=capital_required,
                hourly_profit=profit * safe_quantity,
                roi=profit / cost_each * 100,
                age_seconds=age,
                eligible=magic_level >= 55 if magic_level is not None else None,
            )
        )
    return sorted(results, key=lambda result: result.hourly_profit, reverse=True)


def skill_results(
    mappings: dict[int, ItemMapping],
    points: list[MarketPoint],
    skills: dict[str, int],
    *,
    now: int | None = None,
) -> list[SkillResult]:
    del mappings  # Recipes use stable item IDs so display-name changes cannot remove methods.
    point_by_id = {point.item_id: point for point in points}
    current_time = int(time.time()) if now is None else now
    results = []
    for method in SKILL_METHODS:
        input_points = [(item, point_by_id.get(item.item_id)) for item in method.inputs]
        output_points = [(item, point_by_id.get(item.item_id)) for item in method.outputs]
        if any(point is None for _item_spec, point in input_points + output_points):
            continue
        resolved_inputs = [(item, point) for item, point in input_points if point is not None]
        resolved_outputs = [(item, point) for item, point in output_points if point is not None]
        input_cost = method.fixed_cost + sum(
            conservative_buy_price(point) * item.quantity for item, point in resolved_inputs
        )
        gross_output = sum(
            conservative_sell_price(point) * item.quantity for item, point in resolved_outputs
        )
        output_tax = sum(
            ge_tax(conservative_sell_price(point)) * item.quantity
            for item, point in resolved_outputs
        )
        net_output = gross_output - output_tax
        profit = round(net_output - input_cost)
        ages = [current_time - point.high_time for _item_spec, point in resolved_inputs]
        ages.extend(current_time - point.low_time for _item_spec, point in resolved_outputs)
        if any(age < 0 for age in ages) or max(ages, default=0) > SKILL_MAX_PRICE_AGE_SECONDS:
            continue
        level = skills.get(method.skill)
        results.append(
            SkillResult(
                name=method.name,
                skill=method.skill,
                level=method.level,
                input_cost=round(input_cost),
                output_value=round(net_output),
                profit_action=profit,
                actions_hour=method.actions_hour,
                profit_hour=profit * method.actions_hour,
                eligible=level >= method.level if level is not None else None,
                price_age_seconds=max(ages, default=0),
                notes=method.notes,
                guide_url=SKILL_GUIDES[method.skill],
            )
        )
    return sorted(results, key=lambda result: result.profit_hour, reverse=True)
