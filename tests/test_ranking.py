from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax, offer_targets, plan_flip_portfolio, rank_flips


def test_ge_tax_uses_current_rate_floor_and_cap() -> None:
    assert ge_tax(49) == 0
    assert ge_tax(50) == 1
    assert ge_tax(1_000) == 20
    assert ge_tax(500_000_000) == 5_000_000


def test_ranker_calculates_net_profit_and_filters_stale_prices() -> None:
    now = 2_000_000
    mappings = {1: ItemMapping(1, "Test item", True, 100, None)}
    fresh = MarketPoint(1, 1_200, 1_000, now - 60, now - 90, 50, 600, 1_090, 1_080)
    stale = MarketPoint(1, 2_000, 1_000, now - 4_000, now - 4_000, 50, 600)

    result = rank_flips(mappings, [fresh, stale], cash_stack=100_000, now=now)

    assert len(result) == 1
    assert result[0].tax == 24
    assert result[0].profit_each == 176
    assert result[0].suggested_quantity == 25
    assert result[0].capital_required == 25_000
    assert result[0].potential_profit == 4_400


def test_ranker_expands_position_budget_when_only_one_slot_is_available() -> None:
    now = 2_000_000
    mappings = {1: ItemMapping(1, "Slot-sized item", True, 10_000, None)}
    point = MarketPoint(
        1,
        120,
        100,
        now - 60,
        now - 60,
        20_000,
        100_000,
        110,
        110,
        average_high_5m=120,
        average_low_5m=100,
        average_high_1h=120,
        average_low_1h=100,
    )

    eight_slots = rank_flips(mappings, [point], cash_stack=1_000_000, slot_count=8, now=now)
    one_slot = rank_flips(mappings, [point], cash_stack=1_000_000, slot_count=1, now=now)

    assert eight_slots[0].capital_required == 250_000
    assert one_slot[0].capital_required == 1_000_000


def test_ranker_expands_liquidity_allowance_for_one_slot() -> None:
    now = 2_000_000
    mappings = {1: ItemMapping(1, "Ogre arrow", True, 10_000, None)}
    point = MarketPoint(
        1,
        218,
        192,
        now - 60,
        now - 60,
        2_000,
        13_412,
        205,
        205,
        average_high_5m=218,
        average_low_5m=192,
        average_high_1h=218,
        average_low_1h=192,
    )

    one_slot = rank_flips(mappings, [point], cash_stack=1_000_000, slot_count=1, now=now)

    # The previous fixed 25% hourly-volume cap stopped at 3,353 arrows / 643,776 gp.
    # With one GE slot available, liquidity may expand enough to use the cash stack.
    assert one_slot[0].suggested_quantity == 5_208
    assert one_slot[0].capital_required == 999_936


def test_ranker_rejects_margin_erased_by_tax() -> None:
    now = 2_000_000
    mappings = {1: ItemMapping(1, "Thin margin", True, 100, None)}
    point = MarketPoint(1, 1_010, 1_000, now, now, 100, 1_000)
    assert rank_flips(mappings, [point], cash_stack=100_000, min_roi=0, now=now) == []


def test_ranker_rejects_fragile_one_gp_margin() -> None:
    now = 2_000_000
    mappings = {1: ItemMapping(1, "Water rune", False, 50_000, None)}
    point = MarketPoint(1, 6, 5, now, now, 100_000, 1_000_000, 5.5, 5.5)

    for strategy in ("Quick (up to 1h)", "Balanced (1–4h)", "Overnight (8–12h)"):
        assert (
            rank_flips(
                mappings,
                [point],
                cash_stack=2_000_000,
                strategy=strategy,
                now=now,
            )
            == []
        )


def test_offer_targets_ignore_anomalous_below_market_trade() -> None:
    point = MarketPoint(
        1,
        1_200,
        800,
        2_000_000,
        2_000_000,
        100,
        1_000,
        average_high_5m=1_180,
        average_low_5m=1_100,
    )

    assert offer_targets(point) == (1_100, 1_180)


def test_offer_targets_use_hourly_prices_to_reject_a_five_minute_spike() -> None:
    point = MarketPoint(
        1,
        370_459,
        29_231,
        2_000_000,
        2_000_000,
        20,
        335,
        average_high_5m=370_459,
        average_low_5m=29_231,
        average_high_1h=29_450,
        average_low_1h=29_200,
    )

    assert offer_targets(point) == (29_231, 29_450)


def test_ranker_rejects_a_spread_supported_by_one_trade_on_one_side() -> None:
    now = 2_000_000
    mappings = {1434: ItemMapping(1434, "Dragon mace", True, 70, 30_000)}
    point = MarketPoint(
        1434,
        370_459,
        29_231,
        now,
        now,
        20,
        335,
        average_high_5m=370_459,
        average_low_5m=29_231,
        average_high_1h=370_459,
        average_low_1h=29_231,
        high_volume_5m=1,
        low_volume_5m=19,
        high_volume_1h=1,
        low_volume_1h=334,
    )

    assert rank_flips(mappings, [point], cash_stack=2_000_000, now=now) == []


def _candidate(
    item_id: int,
    price: int,
    maximum: int,
    score: float,
    *,
    profit: int = 10,
    confidence: int = 80,
) -> FlipCandidate:
    return FlipCandidate(
        item_id=item_id,
        name=f"Item {item_id}",
        buy_price=price,
        sell_price=price + profit,
        tax=0,
        profit_each=profit,
        roi=profit / price * 100,
        hourly_volume=10_000,
        projected_volume=40_000,
        buy_limit=maximum,
        suggested_quantity=maximum,
        capital_required=price * maximum,
        potential_profit=profit * maximum,
        confidence=confidence,
        age_seconds=10,
        score=score,
    )


def test_portfolio_uses_available_slots_and_never_overspends() -> None:
    candidates = [_candidate(index, 100, 10_000, 100 - index) for index in range(1, 11)]

    portfolio = plan_flip_portfolio(candidates, cash_stack=2_000_000, slot_count=8)

    assert len(portfolio) == 8
    assert sum(position.capital_required for position in portfolio) <= 2_000_000
    assert {position.item_id for position in portfolio} == set(range(1, 9))


def test_one_slot_prefers_total_expected_profit_over_a_small_high_score_offer() -> None:
    candidates = [
        _candidate(1, 100, 100, 100, profit=20, confidence=90),
        _candidate(2, 100, 1_000, 80, profit=10, confidence=80),
    ]

    portfolio = plan_flip_portfolio(candidates, cash_stack=100_000, slot_count=1)

    assert [position.item_id for position in portfolio] == [2]
    assert portfolio[0].capital_required == 100_000
    assert portfolio[0].potential_profit == 10_000


def test_portfolio_looks_beyond_the_first_ranks_for_usable_capacity() -> None:
    candidates = [
        *[_candidate(index, 100, 1, 100 - index) for index in range(1, 9)],
        _candidate(9, 100, 1_000, 50, profit=9),
    ]

    portfolio = plan_flip_portfolio(candidates, cash_stack=100_000, slot_count=8)

    assert len(portfolio) == 8
    assert 9 in {position.item_id for position in portfolio}
    assert sum(position.capital_required for position in portfolio) == 100_000


def test_portfolio_respects_each_items_independent_liquidity_cap() -> None:
    candidates = [
        _candidate(1, 150, 659, 100),
        _candidate(2, 500, 20_000, 90),
    ]

    portfolio = plan_flip_portfolio(candidates, cash_stack=2_000_000, slot_count=2)
    by_item = {position.item_id: position for position in portfolio}

    assert by_item[1].suggested_quantity == 659
    assert by_item[1].capital_required == 98_850
    assert sum(position.capital_required for position in portfolio) <= 2_000_000


def test_portfolio_leaves_cash_unallocated_when_all_safe_caps_are_reached() -> None:
    candidates = [_candidate(1, 100, 2, 100), _candidate(2, 200, 3, 90)]

    portfolio = plan_flip_portfolio(candidates, cash_stack=10_000, slot_count=8)
    by_item = {position.item_id: position.suggested_quantity for position in portfolio}

    assert by_item == {1: 2, 2: 3}
    assert sum(position.capital_required for position in portfolio) == 800
