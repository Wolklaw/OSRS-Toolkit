from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax, offer_targets, rank_flips


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
