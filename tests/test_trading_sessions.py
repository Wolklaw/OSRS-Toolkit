from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from osrs_toolkit.journal import JournalRepository


def _stamp(**delta: float) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat()


def test_a_ge_buy_outside_a_session_is_filed_as_supplies(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    repository.apply_synced_ge_fill(561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000)

    position = repository.list_tracked()[0]
    assert position.status == "Supplies"


def test_a_ge_buy_inside_a_session_is_tracked_as_a_flip(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session(label="Evening flips")

    repository.apply_synced_ge_fill(1_333, "Rune scimitar", "buy", 10, 15_000, total_quantity=10)

    position = repository.list_tracked()[0]
    assert position.status == "Bought"


def test_an_opened_offer_outside_a_session_is_filed_as_supplies(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    repository.apply_offer_opened(561, "Nature rune", "buy", 1_000, 95)

    position = repository.list_tracked()[0]
    assert position.status == "Supplies"


def test_supplies_keep_absorbing_the_rest_of_their_own_offer(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    repository.apply_synced_ge_fill(561, "Nature rune", "buy", 400, 95, total_quantity=1_000)
    repository.apply_synced_ge_fill(561, "Nature rune", "buy", 600, 95, total_quantity=1_000)

    positions = repository.list_tracked()
    assert len(positions) == 1
    assert positions[0].status == "Supplies"
    assert positions[0].bought_quantity == 1_000


def test_a_supplies_position_gets_no_sell_suggestion(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    repository.apply_synced_ge_fill(
        561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000, suggested_sell_price=120
    )

    position = repository.list_tracked()[0]
    assert position.target_sell == position.target_buy
    assert position.current_sell_suggestion == position.target_buy


def test_always_mode_restores_the_old_unconditional_tracking(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.set_flip_tracking_mode("always")

    repository.apply_synced_ge_fill(1_333, "Rune scimitar", "buy", 10, 15_000, total_quantity=10)

    assert repository.list_tracked()[0].status == "Bought"


def test_an_unknown_tracking_mode_is_refused(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    with pytest.raises(ValueError):
        repository.set_flip_tracking_mode("sometimes")


def test_a_fill_is_judged_by_when_it_happened_not_when_it_imported(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session(started_at=_stamp(hours=2))
    repository.end_trading_session()

    repository.apply_synced_ge_fill(
        1_333,
        "Rune scimitar",
        "buy",
        10,
        15_000,
        total_quantity=10,
        occurred_at=_stamp(hours=1),
    )

    assert repository.list_tracked()[0].status == "Bought"


def test_a_fill_from_before_the_session_opened_is_still_supplies(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session(started_at=_stamp(minutes=10))

    repository.apply_synced_ge_fill(
        561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000, occurred_at=_stamp(hours=3)
    )

    assert repository.list_tracked()[0].status == "Supplies"


def test_backdating_a_session_covers_the_buys_already_made(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session(started_at=_stamp(hours=4))

    repository.apply_synced_ge_fill(
        1_333, "Rune scimitar", "buy", 10, 15_000, total_quantity=10, occurred_at=_stamp(hours=1)
    )

    assert repository.list_tracked()[0].status == "Bought"


def test_starting_a_session_closes_the_one_already_running(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    first = repository.start_trading_session(label="One")

    second = repository.start_trading_session(label="Two")

    assert second.session_id != first.session_id
    running = repository.active_trading_session()
    assert running is not None
    assert running.session_id == second.session_id
    assert len(repository.list_trading_sessions()) == 2


def test_ending_a_session_that_is_not_running_is_not_an_error(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")

    assert repository.end_trading_session() is None


def test_a_session_belongs_to_the_character_that_started_it(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session("alpha")

    assert repository.active_trading_session("alpha") is not None
    assert repository.active_trading_session("beta") is None

    repository.apply_synced_ge_fill(
        561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000, account_hash="beta"
    )

    assert repository.list_tracked("beta")[0].status == "Supplies"


def test_a_session_with_no_character_covers_every_character(tmp_path: Path):
    repository = JournalRepository(tmp_path / "journal.db")
    repository.start_trading_session()

    repository.apply_synced_ge_fill(
        1_333, "Rune scimitar", "buy", 10, 15_000, total_quantity=10, account_hash="beta"
    )

    assert repository.list_tracked("beta")[0].status == "Bought"
