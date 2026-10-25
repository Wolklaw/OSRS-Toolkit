from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.cancellations import cancellation_groups, summarize_cancellations
from osrs_toolkit.journal import JournalRepository, OfferCancelled, OfferEventRecord, OfferOpened

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _event(
    event_id: str,
    kind: str,
    minute: int,
    *,
    strategy: str | None = "Overnight",
    restored: bool = False,
    side: str = "buy",
    price: int = 1000,
    quantity: int = 10,
) -> OfferEventRecord:
    return OfferEventRecord(
        event_id=event_id,
        account_hash="h",
        occurred_at=f"2026-10-25T10:{minute:02d}:00+00:00",
        kind=kind,
        side=side,
        item_id=4151,
        item_name="Whip",
        offer_price=price,
        total_quantity=quantity,
        restored=restored,
        strategy=strategy,
    )


def test_a_cancel_is_matched_to_its_opening_and_the_wait_is_measured() -> None:
    groups = cancellation_groups(
        [_event("o1", "opened", 0), _event("c1", "cancelled", 6)], "All time", NOW
    )

    assert len(groups) == 1
    assert groups[0].cancelled == 1
    assert groups[0].median_wait_minutes == 6


def test_a_restored_opening_gives_no_wait_and_is_not_counted_as_placed() -> None:
    groups = cancellation_groups(
        [_event("o1", "opened", 0, restored=True), _event("c1", "cancelled", 6)],
        "All time",
        NOW,
    )

    assert groups[0].opened == 0
    assert groups[0].cancelled == 1
    assert groups[0].median_wait_minutes is None


def test_the_rate_waits_for_enough_offers_then_reports_the_share_cancelled() -> None:
    few = [_event("o1", "opened", 0), _event("c1", "cancelled", 5)]
    assert cancellation_groups(few, "All time", NOW)[0].cancel_rate is None

    many = [_event(f"o{i}", "opened", i * 2, price=1000 + i) for i in range(5)]
    many += [
        _event("c1", "cancelled", 30, price=1000),
        _event("c2", "cancelled", 31, price=1001),
    ]

    assert cancellation_groups(many, "All time", NOW)[0].cancel_rate == 40.0


def test_a_missing_strategy_falls_back_to_the_openings_then_to_a_label() -> None:
    groups = cancellation_groups(
        [
            _event("o1", "opened", 0, strategy="Quick"),
            _event("c1", "cancelled", 4, strategy=None),
            _event("c2", "cancelled", 8, strategy=None, price=5),
        ],
        "All time",
        NOW,
    )

    assert {group.label for group in groups} == {"Quick", "No strategy"}


def test_the_summary_adds_up_the_groups() -> None:
    groups = cancellation_groups(
        [
            _event("o1", "opened", 0),
            _event("c1", "cancelled", 6),
            _event("o2", "opened", 10, side="sell"),
            _event("c2", "cancelled", 30, side="sell"),
        ],
        "All time",
        NOW,
    )

    summary = summarize_cancellations(groups)

    assert (summary.opened, summary.cancelled) == (2, 2)
    assert summary.median_wait_minutes == 13


def test_offer_events_are_stored_once_with_the_strategy(tmp_path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    opened = OfferOpened("e1", "h", "n", "2026-10-25T10:00:00+00:00", "buy", 4151, "Whip", 1000, 10)
    cancelled = OfferCancelled(
        "e2", "h", "n", "2026-10-25T10:05:00+00:00", "buy", 4151, "Whip", 1000, 10
    )

    assert repository.record_offer_event(opened, "Overnight")
    assert not repository.record_offer_event(opened, "Overnight")
    repository.record_offer_event(cancelled, None)

    stored = repository.list_offer_events()
    assert [row.kind for row in stored] == ["opened", "cancelled"]
    assert stored[0].strategy == "Overnight"
    assert stored[1].strategy is None
