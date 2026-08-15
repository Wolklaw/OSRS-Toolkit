import sqlite3
from pathlib import Path

import pytest

from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade


def test_journal_persists_and_calculates_realized_profit(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    trade_id = repository.add("Test item", 10, 1_000, 1_200)

    trade = repository.list_all()[0]
    assert trade.trade_id == trade_id
    assert trade.tax_each == 24
    assert trade.invested == 10_000
    assert trade.profit == 1_760
    assert trade.roi == pytest.approx(17.6)

    repository.delete(trade_id)
    assert repository.list_all() == []


def test_recommendation_can_be_tracked_through_completion(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Tracked item", 100, 1_000, 1_200)

    pending = repository.list_tracked()[0]
    assert pending.position_id == position_id
    assert pending.status == "Pending buy"
    assert pending.strategy == "Balanced (1–4h)"
    assert pending.estimated_profit == 17_600
    assert pending.realized_profit is None

    repository.update_tracked(position_id, "Completed", 1_010, 1_190)
    completed = repository.list_tracked()[0]
    assert completed.realized_profit == 15_700
    assert completed.sold_quantity == 100
    assert completed.remaining_quantity == 0


def test_tracked_trade_supports_multiple_sale_prices_and_partial_progress(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Variable sale", 10, 1_800, 2_100)

    repository.update_tracked(
        position_id,
        "Partially sold",
        1_800,
        None,
        [(5, 2_002)],
    )
    partial = repository.list_tracked()[0]
    assert partial.sold_quantity == 5
    assert partial.remaining_quantity == 5
    assert partial.average_sell_price == 2_002
    assert partial.realized_profit == 810

    repository.update_tracked(
        position_id,
        "Completed",
        1_800,
        None,
        [(5, 2_002), (5, 1_900)],
    )
    completed = repository.list_tracked()[0]
    assert completed.sold_quantity == 10
    assert completed.remaining_quantity == 0
    assert completed.average_sell_price == 1_951
    assert completed.realized_profit == 1_120


def test_completed_trade_rejects_unaccounted_sale_quantity(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Incomplete sale", 10, 1_800, 2_100)

    with pytest.raises(ValueError, match="full quantity"):
        repository.update_tracked(
            position_id,
            "Completed",
            1_800,
            None,
            [(5, 2_002)],
        )


def test_overnight_trade_preserves_targets_and_records_daily_suggestion(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(
        1,
        "Overnight item",
        10,
        1_800,
        2_100,
        "Overnight (8–12h)",
    )

    repository.review_suggestion(
        position_id,
        1_750,
        2_020,
        "2099-01-02T08:00:00+00:00",
    )
    trade = repository.list_tracked()[0]

    assert trade.target_buy == 1_800
    assert trade.target_sell == 2_100
    assert trade.buy_suggestion == 1_750
    assert trade.sell_suggestion == 2_020
    assert trade.suggestion_was_refreshed is True


def test_legacy_malformed_balanced_strategy_is_normalized(tmp_path: Path) -> None:
    database_path = tmp_path / "journal.db"
    repository = JournalRepository(database_path)
    position_id = repository.track(1, "Legacy strategy", 10, 100, 120)
    malformed_strategy = "Balanced (1\u00e2\u20ac\u201c4h)"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tracked_trades SET strategy = ? WHERE position_id = ?",
            (malformed_strategy, position_id),
        )

    repaired = JournalRepository(database_path).list_tracked()[0]
    assert repaired.strategy == "Balanced (1–4h)"


def test_tracked_trade_supports_multiple_buy_prices(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Variable buy", 10, 1_800, 2_100)

    repository.update_tracked(
        position_id,
        "Bought",
        None,
        None,
        None,
        [(6, 1_700), (4, 1_900)],
    )
    bought = repository.list_tracked()[0]
    assert bought.bought_quantity == 10
    assert bought.average_buy_price == 1_780
    assert bought.actual_buy == 1_780

    repository.update_tracked(
        position_id,
        "Completed",
        None,
        None,
        [(10, 2_050)],
        [(6, 1_700), (4, 1_900)],
    )
    completed = repository.list_tracked()[0]
    assert completed.realized_profit == (2_050 - 1_780 - 41) * 10


def test_completed_trade_rejects_unaccounted_buy_quantity(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Incomplete buy", 10, 1_800, 2_100)

    with pytest.raises(ValueError, match="buy fills"):
        repository.update_tracked(
            position_id,
            "Completed",
            None,
            None,
            [(10, 2_050)],
            [(6, 1_700)],
        )


def test_cancelled_partial_buy_can_be_resized_and_sold(tmp_path: Path) -> None:
    """A buy order cancelled mid-fill leaves a smaller position that must still be sold."""
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Cancelled partial", 100, 1_500, 1_700)

    # Only 40 of the intended 100 filled before the order was cancelled.
    repository.update_tracked(
        position_id,
        "Bought",
        None,
        None,
        None,
        [(40, 1_520)],
        40,
    )
    resized = repository.list_tracked()[0]
    assert resized.quantity == 40
    assert resized.bought_quantity == 40
    assert resized.average_buy_price == 1_520

    repository.update_tracked(
        position_id,
        "Completed",
        None,
        None,
        [(40, 1_690)],
        [(40, 1_520)],
    )
    completed = repository.list_tracked()[0]
    assert completed.quantity == 40
    assert completed.sold_quantity == 40
    assert completed.realized_profit is not None


def test_quantity_cannot_shrink_below_recorded_fills(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Guarded shrink", 50, 100, 150)

    with pytest.raises(ValueError, match="exceed the tracked quantity"):
        repository.update_tracked(
            position_id,
            "Bought",
            None,
            None,
            None,
            [(30, 110)],
            20,
        )


def test_completed_at_is_set_on_terminal_status_and_cleared_when_reopened(
    tmp_path: Path,
) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(1, "Terminal timestamps", 10, 100, 150)
    assert repository.list_tracked()[0].completed_at is None

    repository.update_tracked(position_id, "Cancelled", None, None)
    cancelled = repository.list_tracked()[0]
    assert cancelled.completed_at is not None

    repository.update_tracked(position_id, "Bought", None, None, None, [(10, 105)])
    reopened = repository.list_tracked()[0]
    assert reopened.completed_at is None


def _synced_trade(event_id: str, occurred_at: str = "2026-08-14T12:00:00+00:00") -> SyncedTrade:
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash="hash",
        account_name="Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(flow="received", item_id=4151, item_name="Whip", quantity=1, unit_value=1_500),
            SyncedItem(flow="given", item_id=995, item_name="Coins", quantity=1_500, unit_value=1),
        ),
    )


def test_add_synced_trades_batches_inserts_and_reports_duplicates(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    event_id = "11111111-1111-1111-1111-111111111111"
    other_id = "22222222-2222-2222-2222-222222222222"
    trade_one = _synced_trade(event_id)
    trade_two = _synced_trade(other_id)

    results = repository.add_synced_trades([trade_one, trade_two, trade_one])
    assert results == [True, True, False]

    fetched = repository.get_synced_trade(event_id)
    assert fetched is not None
    assert len(fetched.items) == 2
    assert repository.get_synced_trade("missing-event") is None
    assert len(repository.list_synced_trades()) == 2


def test_default_journal_recovers_from_version_independent_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_data = tmp_path / "local"
    roaming_data = tmp_path / "roaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_data))
    monkeypatch.setenv("APPDATA", str(roaming_data))

    repository = JournalRepository()
    repository.add("Preserved item", 5, 100, 150)

    # A later launch snapshots the populated database outside the install/data location.
    JournalRepository()
    database_path = local_data / "OSRSToolkit" / "data" / "toolkit.db"
    database_path.unlink()

    recovered = JournalRepository()
    assert recovered.list_all()[0].item_name == "Preserved item"
    assert list((roaming_data / "OSRSToolkit" / "backups").glob("toolkit-*.db"))
