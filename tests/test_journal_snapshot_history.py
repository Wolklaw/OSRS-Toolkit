from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.journal import JournalRepository, LoadoutItem, LoadoutSnapshot

ACCOUNT_HASH = "123456789"
ACCOUNT_NAME = "Example Player"


def _repository(tmp_path: Path) -> JournalRepository:
    return JournalRepository(tmp_path / "journal.db")


def _snapshot(captured_at: str, coins: int = 1_000_000) -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash=ACCOUNT_HASH,
        account_name=ACCOUNT_NAME,
        captured_at=captured_at,
        equipment=(),
        inventory=(),
        bank=(LoadoutItem(item_id=995, item_name="Coins", quantity=coins, unit_value=1),),
        skills={"Attack": 75},
        xp={"Attack": 1_210_421},
    )


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def _midday_ago(days: int) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )


def _midweek_ago(days: int) -> datetime:
    anchor = _midday_ago(days)
    return anchor - timedelta(days=anchor.isoweekday() - 3)


def test_every_snapshot_is_kept_and_the_newest_row_still_reads_as_before(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-10-01T10:00:00Z", coins=1))
    repository.save_loadout_snapshot(_snapshot("2026-10-02T10:00:00Z", coins=2))
    repository.save_loadout_snapshot(_snapshot("2026-10-03T10:00:00Z", coins=3))

    history = repository.list_loadout_snapshots(ACCOUNT_HASH)

    assert [point.captured_at for point in history] == [
        "2026-10-01T10:00:00Z",
        "2026-10-02T10:00:00Z",
        "2026-10-03T10:00:00Z",
    ]
    assert [point.total_value for point in history] == [1, 2, 3]
    assert history[0].xp == {"Attack": 1_210_421}

    newest = repository.get_loadout_snapshot(ACCOUNT_HASH)
    assert newest is not None
    assert newest.captured_at == "2026-10-03T10:00:00Z"
    assert newest.total_value == 3


def test_history_is_kept_per_character(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-10-01T10:00:00Z"))
    other = LoadoutSnapshot(
        account_hash="987654321",
        account_name="Alt",
        captured_at="2026-10-01T11:00:00Z",
        equipment=(),
        inventory=(),
        bank=(),
        skills={},
    )
    repository.save_loadout_snapshot(other)

    assert len(repository.list_loadout_snapshots(ACCOUNT_HASH)) == 1
    assert len(repository.list_loadout_snapshots("987654321")) == 1
    assert len(repository.list_loadout_snapshots()) == 2


def test_the_same_snapshot_delivered_twice_is_stored_once(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-10-01T10:00:00Z", coins=1))
    repository.save_loadout_snapshot(_snapshot("2026-10-01T10:00:00Z", coins=5))

    history = repository.list_loadout_snapshots(ACCOUNT_HASH)

    assert len(history) == 1
    assert history[0].total_value == 5


def test_bounds_are_compared_as_instants_rather_than_text(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.save_loadout_snapshot(_snapshot("2026-10-01T10:00:00Z", coins=1))
    repository.save_loadout_snapshot(_snapshot("2026-10-01T12:00:00+00:00", coins=2))
    repository.save_loadout_snapshot(_snapshot("2026-10-01T16:00:00Z", coins=3))

    window = repository.list_loadout_snapshots(
        ACCOUNT_HASH, since="2026-10-01T11:00:00+00:00", until="2026-10-01T13:00:00Z"
    )

    assert [point.total_value for point in window] == [2]
    assert [point.total_value for point in repository.list_loadout_snapshots(ACCOUNT_HASH)] == [
        1,
        2,
        3,
    ]


def test_recent_history_is_kept_whole_and_older_history_thins(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    for hour in range(3):
        repository.save_loadout_snapshot(_snapshot(_ago(1 + hour / 24), coins=100 + hour))
    one_old_day = _midday_ago(60)
    for hour in range(3):
        moment = one_old_day + timedelta(hours=hour)
        repository.save_loadout_snapshot(_snapshot(moment.isoformat(), coins=200 + hour))
    one_old_week = _midweek_ago(400)
    for day in range(3):
        moment = one_old_week + timedelta(days=day)
        repository.save_loadout_snapshot(_snapshot(moment.isoformat(), coins=300 + day))

    kept = {point.total_value for point in repository.list_loadout_snapshots(ACCOUNT_HASH)}

    assert {100, 101, 102} <= kept
    assert kept & {200, 201, 202} == {202}
    assert kept & {300, 301, 302} == {302}


def test_a_journal_written_before_the_history_table_is_seeded_from_its_one_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "journal.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE loadout_snapshots (account_hash TEXT PRIMARY KEY,"
            " account_name TEXT NOT NULL, captured_at TEXT NOT NULL,"
            " equipment_json TEXT NOT NULL, inventory_json TEXT NOT NULL,"
            " bank_json TEXT NOT NULL, skills_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO loadout_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ACCOUNT_HASH,
                ACCOUNT_NAME,
                "2026-08-01T00:00:00Z",
                "[]",
                "[]",
                '[{"item_id":995,"item_name":"Coins","quantity":7,"unit_value":1}]',
                '{"Attack": 70}',
            ),
        )

    repository = JournalRepository(db_path)
    history = repository.list_loadout_snapshots(ACCOUNT_HASH)

    assert len(history) == 1
    assert history[0].captured_at == "2026-08-01T00:00:00Z"
    assert history[0].total_value == 7
    assert history[0].xp == {}

    history_after_reopen = JournalRepository(db_path).list_loadout_snapshots(ACCOUNT_HASH)
    assert len(history_after_reopen) == 1
