import sqlite3
from pathlib import Path

import pytest

from osrs_toolkit.journal import JournalRepository


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
