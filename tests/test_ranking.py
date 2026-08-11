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
        assert rank_flips(
            mappings,
            [point],
            cash_stack=2_000_000,
            strategy=strategy,
            now=now,
        ) == []


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


def _candidate(item_id: int, price: int, maximum: int, score: float) -> FlipCandidate:
    return FlipCandidate(
        item_id=item_id,
        name=f"Item {item_id}",
        buy_price=price,
        sell_price=price + 10,
        tax=0,
        profit_each=10,
        roi=10 / price * 100,
        hourly_volume=10_000,
        projected_volume=40_000,
        buy_limit=maximum,
        suggested_quantity=maximum,
        capital_required=price * maximum,
        potential_profit=10 * maximum,
        confidence=80,
        age_seconds=10,
        score=score,
    )


def test_portfolio_uses_available_slots_and_never_overspends() -> None:
    candidates = [_candidate(index, 100, 10_000, 100 - index) for index in range(1, 11)]

    portfolio = plan_flip_portfolio(candidates, cash_stack=2_000_000, slot_count=8)

    assert len(portfolio) == 8
    assert sum(position.capital_required for position in portfolio) <= 2_000_000
    assert {position.item_id for position in portfolio} == set(range(1, 9))


def test_portfolio_respects_each_items_independent_liquidity_cap() -> None:
    candidates = [
        _candidate(1, 150, 659, 100),
        _candidate(2, 500, 20_000, 90),
    ]

    portfolio = plan_flip_portfolio(candidates, cash_stack=2_000_000, slot_count=2)

    assert portfolio[0].suggested_quantity == 659
    assert portfolio[0].capital_required == 98_850
    assert sum(position.capital_required for position in portfolio) <= 2_000_000


def test_portfolio_leaves_cash_unallocated_when_all_safe_caps_are_reached() -> None:
    candidates = [_candidate(1, 100, 2, 100), _candidate(2, 200, 3, 90)]

    portfolio = plan_flip_portfolio(candidates, cash_stack=10_000, slot_count=8)

    assert [position.suggested_quantity for position in portfolio] == [2, 3]
    assert sum(position.capital_required for position in portfolio) == 800
