from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.fill_times import fill_observations, fill_time_groups
from osrs_toolkit.journal import OfferEventRecord, SyncedItem, SyncedTrade

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _stamp(minute: int, hour: int = 10) -> str:
    return f"2026-10-25T{hour:02d}:{minute:02d}:00+00:00"


def _opened(event_id: str, minute: int, *, restored: bool = False, price: int = 1000):
    return OfferEventRecord(
        event_id, "h", _stamp(minute), "opened", "buy", 4151, "Whip", price, 10, restored, None
    )


def _cancelled(event_id: str, minute: int, price: int = 1000):
    return OfferEventRecord(
        event_id, "h", _stamp(minute), "cancelled", "buy", 4151, "Whip", price, 10, False, None
    )


def _fill(event_id: str, minute: int, state: str, price: int = 1000) -> SyncedTrade:
    return SyncedTrade(
        event_id=event_id,
        occurred_at=_stamp(minute),
        event_type="ge_fill",
        account_hash="h",
        account_name="n",
        counterparty=None,
        direction="buy",
        metadata={"offer_price": price, "total_quantity": 10, "offer_state": state},
        items=(
            SyncedItem("given", 995, "Coins", 5_000, 1),
            SyncedItem("received", 4151, "Whip", 5, 1000),
        ),
    )


def test_an_offer_is_timed_from_opening_to_first_fill_and_to_completion() -> None:
    observed = fill_observations(
        [_opened("o", 0)], [_fill("f1", 4, "BUYING"), _fill("f2", 9, "BOUGHT")]
    )

    assert len(observed) == 1
    assert observed[0].first_fill_minutes == 4
    assert observed[0].complete_minutes == 9


def test_a_cancelled_offer_is_not_an_observation() -> None:
    observed = fill_observations(
        [_opened("o", 0), _cancelled("c", 5)], [_fill("f", 3, "BUYING"), _fill("g", 8, "BOUGHT")]
    )

    assert observed == []


def test_a_restored_opening_cannot_be_timed() -> None:
    assert fill_observations([_opened("o", 0, restored=True)], [_fill("f", 4, "BOUGHT")]) == []


def test_fills_at_a_different_price_do_not_belong_to_the_offer() -> None:
    assert fill_observations([_opened("o", 0)], [_fill("f", 4, "BOUGHT", price=999)]) == []


def test_groups_wait_for_enough_offers_and_report_medians() -> None:
    observed = []
    for index, minutes in enumerate((2, 6, 10)):
        base = index * 12
        observed += fill_observations(
            [_opened(f"o{index}", base)],
            [_fill(f"f{index}", base + minutes, "BOUGHT")],
        )

    assert fill_time_groups(observed[:2], "All time", NOW) == []

    groups = fill_time_groups(observed, "All time", NOW)

    assert len(groups) == 1
    assert groups[0].median_complete_minutes == 6
    assert (groups[0].fastest_minutes, groups[0].slowest_minutes) == (2, 10)
