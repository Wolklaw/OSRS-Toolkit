from osrs_toolkit.calculators import (
    SKILL_GUIDES,
    SKILL_METHODS,
    alch_candidates,
    conservative_buy_price,
    conservative_sell_price,
    skill_results,
)
from osrs_toolkit.models import ItemMapping, MarketPoint

NOW = 2_000_000


def _point(
    item_id: int,
    high: int,
    low: int,
    *,
    volume: int = 10_000,
    age: int = 60,
    average_high_5m: int | None = None,
    average_low_5m: int | None = None,
    average_high_1h: int | None = None,
    average_low_1h: int | None = None,
) -> MarketPoint:
    return MarketPoint(
        item_id=item_id,
        high=high,
        low=low,
        high_time=NOW - age,
        low_time=NOW - age,
        volume_5m=volume // 12,
        volume_1h=volume,
        average_high_5m=average_high_5m,
        average_low_5m=average_low_5m,
        average_high_1h=average_high_1h,
        average_low_1h=average_low_1h,
    )


def test_conservative_prices_do_not_use_the_most_optimistic_trade() -> None:
    point = _point(
        1,
        32_000,
        38_000,
        average_high_5m=36_000,
        average_high_1h=35_500,
        average_low_5m=37_000,
        average_low_1h=36_500,
    )

    assert conservative_buy_price(point) == 36_000
    assert conservative_sell_price(point) == 36_500


def test_alch_finder_rejects_profit_created_by_an_old_low_trade() -> None:
    mappings = {
        1: ItemMapping(1, "Misleading item", True, 100, 34_000),
        561: ItemMapping(561, "Nature rune", False, 18_000, None),
    }
    points = [
        _point(1, 32_000, 31_000, average_high_5m=36_000, average_high_1h=35_000),
        _point(561, 150, 148, average_high_5m=151, average_high_1h=149),
    ]

    assert alch_candidates(mappings, points, now=NOW, policy="Safer") == []


def test_alch_quantity_respects_budget_volume_and_buy_limit() -> None:
    mappings = {
        1: ItemMapping(1, "Safe item", True, 70, 1_500),
        561: ItemMapping(561, "Nature rune", False, 18_000, None),
    }
    points = [_point(1, 1_000, 990, volume=1_000), _point(561, 100, 99)]

    result = alch_candidates(
        mappings,
        points,
        cash_stack=20_000,
        policy="Safer",
        magic_level=54,
        now=NOW,
    )[0]

    assert result.safe_quantity == 18
    assert result.capital_required == 19_800
    assert result.profit == 400
    assert result.hourly_profit == 7_200
    assert result.eligible is False


def test_safer_alch_policy_rejects_stale_and_thinly_traded_items() -> None:
    mappings = {
        1: ItemMapping(1, "Stale item", True, 100, 2_000),
        2: ItemMapping(2, "Thin item", True, 100, 2_000),
        561: ItemMapping(561, "Nature rune", False, 18_000, None),
    }
    points = [
        _point(1, 1_000, 990, age=601),
        _point(2, 1_000, 990, volume=199),
        _point(561, 100, 99),
    ]

    assert alch_candidates(mappings, points, policy="Safer", now=NOW) == []
    assert len(alch_candidates(mappings, points, policy="Show all", now=NOW)) == 2


def test_skilling_catalogue_spans_ten_skills_and_uses_stable_item_ids() -> None:
    assert len(SKILL_METHODS) >= 80
    assert {method.skill for method in SKILL_METHODS} == {
        "Cooking",
        "Crafting",
        "Fishing",
        "Fletching",
        "Herblore",
        "Hunter",
        "Magic",
        "Mining",
        "Smithing",
        "Woodcutting",
    }
    assert SKILL_GUIDES == {
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
    assert set(SKILL_GUIDES) == {method.skill for method in SKILL_METHODS}

    mappings = {
        2: ItemMapping(2, "Steel cannonball", True, 11_000, 3),
        2353: ItemMapping(2353, "Renamed steel bar", True, 10_000, 300),
    }
    rows = skill_results(
        mappings,
        [_point(2353, 600, 590), _point(2, 200, 195)],
        {"Smithing": 34},
        now=NOW,
    )

    cannonballs = [row for row in rows if row.name.startswith("Smith steel cannonballs")]
    assert len(cannonballs) == 2
    assert all(row.eligible is False for row in cannonballs)
    assert all(row.guide_url == SKILL_GUIDES["Smithing"] for row in cannonballs)


def test_skilling_recipe_supports_multiple_inputs_and_output_tax() -> None:
    rows = skill_results(
        {},
        [
            _point(1637, 400, 390),
            _point(564, 100, 98),
            _point(2550, 650, 640),
        ],
        {"Magic": 7},
        now=NOW,
    )
    recoil = next(row for row in rows if row.name == "Enchant sapphire rings")

    assert recoil.input_cost == 500
    assert recoil.output_value == 628
    assert recoil.profit_action == 128
    assert recoil.eligible is True
