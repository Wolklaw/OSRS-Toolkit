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
    assert pending.estimated_profit == 17_600
    assert pending.realized_profit is None

    repository.update_tracked(position_id, "Completed", 1_010, 1_190)
    completed = repository.list_tracked()[0]
    assert completed.realized_profit == 15_700
