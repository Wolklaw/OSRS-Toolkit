from __future__ import annotations

from osrs_toolkit.journal import LoadoutItem, LoadoutSnapshot, NpcLootRecord
from osrs_toolkit.models import MarketPoint
from osrs_toolkit.pvm import (
    PVM_ACTIVITIES,
    assess_all,
    assess_readiness,
    estimate_gp_per_hour,
    observed_gp_per_hour,
)


def _snapshot(skills: dict[str, int], item_names: list[str]) -> LoadoutSnapshot:
    bank = tuple(
        LoadoutItem(item_id=index, item_name=name, quantity=1, unit_value=1)
        for index, name in enumerate(item_names)
    )
    return LoadoutSnapshot(
        account_hash="hash",
        account_name="Tester",
        captured_at="2026-08-15T00:00:00+00:00",
        equipment=(),
        inventory=(),
        bank=bank,
        skills=skills,
    )


def test_missing_snapshot_reports_nothing_either_way() -> None:
    """Without a loadout there is nothing to compare, and an account that has told us
    nothing is not an account that owns nothing."""
    king_black_dragon = next(a for a in PVM_ACTIVITIES if a.name == "King Black Dragon")

    readiness = assess_readiness(king_black_dragon, None)

    assert readiness.assessed is False
    assert readiness.is_ready is False, "unchecked must not read as ready either"
    assert readiness.missing_skills == ()
    assert readiness.missing_gear == ()


def test_a_checked_activity_says_so() -> None:
    king_black_dragon = next(a for a in PVM_ACTIVITIES if a.name == "King Black Dragon")

    readiness = assess_readiness(king_black_dragon, _snapshot(skills={}, item_names=[]))

    assert readiness.assessed is True
    assert readiness.missing_gear, "an empty bank really is missing the gear"


def test_fully_equipped_account_is_ready() -> None:
    king_black_dragon = next(a for a in PVM_ACTIVITIES if a.name == "King Black Dragon")
    snapshot = _snapshot(
        skills={"Hitpoints": 70, "Prayer": 50},
        item_names=["Dragonfire shield"],
    )

    readiness = assess_readiness(king_black_dragon, snapshot)

    assert readiness.is_ready is True
    assert readiness.missing_skills == ()
    assert readiness.missing_gear == ()


def test_gear_match_is_case_insensitive_and_accepts_any_alternative() -> None:
    king_black_dragon = next(a for a in PVM_ACTIVITIES if a.name == "King Black Dragon")
    snapshot = _snapshot(
        skills={"Hitpoints": 70, "Prayer": 50},
        item_names=["ANTI-DRAGON SHIELD"],
    )

    readiness = assess_readiness(king_black_dragon, snapshot)

    assert readiness.missing_gear == ()


def test_gear_match_ignores_a_potion_dose_suffix() -> None:
    """Regression: RuneLite reports real potion names with a dose count, e.g.
    "Prayer potion(4)" — checklist requirements are written without one and must still match."""
    barrows = next(a for a in PVM_ACTIVITIES if a.name == "Barrows")
    snapshot = _snapshot(
        skills={"Hitpoints": 60, "Prayer": 50},
        item_names=["Abyssal whip", "Prayer potion(4)"],
    )

    readiness = assess_readiness(barrows, snapshot)

    assert readiness.is_ready is True
    assert readiness.missing_gear == ()


def test_underleveled_skill_is_reported_with_both_levels() -> None:
    king_black_dragon = next(a for a in PVM_ACTIVITIES if a.name == "King Black Dragon")
    snapshot = _snapshot(skills={"Hitpoints": 40, "Prayer": 50}, item_names=["Dragonfire shield"])

    readiness = assess_readiness(king_black_dragon, snapshot)

    assert len(readiness.missing_skills) == 1
    missing = readiness.missing_skills[0]
    assert missing.skill == "Hitpoints"
    assert missing.required_level == king_black_dragon.skill_requirements["Hitpoints"]
    assert missing.current_level == 40


def test_assess_all_covers_every_curated_activity() -> None:
    snapshot = _snapshot(skills={}, item_names=[])

    results = assess_all(snapshot)

    assert len(results) == len(PVM_ACTIVITIES)
    assert {result.activity.name for result in results} == {a.name for a in PVM_ACTIVITIES}


def test_every_activity_has_a_wiki_link_and_positive_gp_estimate() -> None:
    for activity in PVM_ACTIVITIES:
        assert activity.wiki_url.startswith("https://oldschool.runescape.wiki/")
        assert activity.gross_gp_per_hour > 0
        assert activity.skill_requirements
        assert activity.gear
        assert activity.supplies


def test_activity_names_are_unique() -> None:
    names = [activity.name for activity in PVM_ACTIVITIES]
    assert len(names) == len(set(names))


def test_gp_estimate_nets_live_supply_cost_from_the_gross_baseline() -> None:
    barrows = next(a for a in PVM_ACTIVITIES if a.name == "Barrows")
    now = 1_000_000
    points = [
        MarketPoint(
            item_id=supply.item_id,
            high=100,
            low=90,
            high_time=now - 60,
            low_time=now - 60,
            volume_5m=500,
            volume_1h=5_000,
        )
        for supply in barrows.supplies
    ]

    estimate = estimate_gp_per_hour(barrows, {}, points, now=now)

    expected_cost = round(sum(100 * supply.quantity_per_hour for supply in barrows.supplies))
    assert estimate.priced is True
    assert estimate.gross_gp_per_hour == barrows.gross_gp_per_hour
    assert estimate.supply_cost_hour == expected_cost
    assert estimate.net_gp_per_hour == barrows.gross_gp_per_hour - expected_cost


def test_gp_estimate_falls_back_to_the_gross_figure_without_live_prices() -> None:
    """A missing snapshot (e.g. market data hasn't loaded yet) must not crash the tab or
    hide an activity's numbers — it should just show the community baseline as-is."""
    barrows = next(a for a in PVM_ACTIVITIES if a.name == "Barrows")

    estimate = estimate_gp_per_hour(barrows, {}, [], now=1_000_000)

    assert estimate.priced is False
    assert estimate.supply_cost_hour == 0
    assert estimate.net_gp_per_hour == barrows.gross_gp_per_hour


def test_gp_estimate_prices_what_it_can_when_one_supply_item_is_missing() -> None:
    barrows = next(a for a in PVM_ACTIVITIES if a.name == "Barrows")
    priced_supply = barrows.supplies[0]
    points = [
        MarketPoint(
            item_id=priced_supply.item_id,
            high=100,
            low=90,
            high_time=999_940,
            low_time=999_940,
            volume_5m=500,
            volume_1h=5_000,
        )
    ]

    estimate = estimate_gp_per_hour(barrows, {}, points, now=1_000_000)

    # A partial price picture is treated the same as none: showing a supply cost that only
    # accounts for some of what's consumed would understate the real cost, so the estimate
    # falls back to the gross figure rather than silently netting an incomplete number.
    assert estimate.priced is False
    assert estimate.net_gp_per_hour == barrows.gross_gp_per_hour


def _loot(event_id: str, occurred_at: str, value: int) -> NpcLootRecord:
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=occurred_at,
        account_hash="hash",
        account_name="Tester",
        npc_name="Vorkath",
        items=(LoadoutItem(item_id=995, item_name="Coins", quantity=value, unit_value=1),),
    )


def test_observed_gp_per_hour_needs_at_least_two_events() -> None:
    assert observed_gp_per_hour([]) is None
    assert observed_gp_per_hour([_loot("a", "2026-08-15T00:00:00+00:00", 100_000)]) is None


def test_observed_gp_per_hour_divides_total_value_by_elapsed_time() -> None:
    events = [
        _loot("a", "2026-08-15T00:00:00+00:00", 1_000_000),
        _loot("b", "2026-08-15T01:30:00+00:00", 1_000_000),
    ]

    # 2,000,000 gp over 1.5 hours.
    assert observed_gp_per_hour(events) == 1_333_333


def test_observed_gp_per_hour_is_order_independent() -> None:
    later = _loot("a", "2026-08-15T02:00:00+00:00", 500_000)
    earlier = _loot("b", "2026-08-15T00:00:00+00:00", 500_000)

    assert observed_gp_per_hour([later, earlier]) == 500_000

