"""The migration that gives journal rows a name other machines can recognise.

``trades`` and ``tracked_trades`` are the only two tables without a global identity of their
own -- everything the plugin produces already carries an ``event_id``, and a loadout is keyed
by character. These cover the migration that adds one, on the two things that matter about it:
that an existing journal survives it intact, and that running it twice changes nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from osrs_toolkit.journal import JournalRepository

SYNC_COLUMNS = {"sync_uid", "updated_at", "deleted_at"}


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _legacy_journal(path: Path) -> None:
    """A journal shaped the way one was before any of this existed: the two tables at their
    original columns, with rows in them."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE trades (trade_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " recorded_at TEXT NOT NULL, item_name TEXT NOT NULL,"
            " quantity INTEGER NOT NULL CHECK (quantity > 0),"
            " buy_price INTEGER NOT NULL CHECK (buy_price >= 0),"
            " sell_price INTEGER NOT NULL CHECK (sell_price >= 0))"
        )
        connection.execute(
            "CREATE TABLE tracked_trades (position_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TEXT NOT NULL, item_id INTEGER, item_name TEXT NOT NULL,"
            " quantity INTEGER NOT NULL CHECK (quantity > 0),"
            " target_buy INTEGER NOT NULL CHECK (target_buy >= 0),"
            " target_sell INTEGER NOT NULL CHECK (target_sell >= 0),"
            " actual_buy INTEGER, actual_sell INTEGER, status TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO trades (recorded_at, item_name, quantity, buy_price, sell_price)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                ("2026-08-11T06:00:00+00:00", "Abyssal whip", 1, 1_500_000, 1_650_000),
                ("2026-08-12T06:00:00+00:00", "Dragon bones", 500, 2_000, 2_600),
            ],
        )
        connection.executemany(
            "INSERT INTO tracked_trades (created_at, item_name, quantity, target_buy,"
            " target_sell, status) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2026-08-11T07:00:00+00:00", "Rune scimitar", 10, 15_000, 16_500, "Completed"),
                ("2026-08-12T07:00:00+00:00", "Magic logs", 1000, 1_000, 1_150, "Pending buy"),
            ],
        )


def test_an_old_journal_gains_the_columns_without_losing_rows(tmp_path):
    path = tmp_path / "toolkit.db"
    _legacy_journal(path)

    JournalRepository(path)

    assert SYNC_COLUMNS <= _columns(path, "trades")
    assert SYNC_COLUMNS <= _columns(path, "tracked_trades")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM tracked_trades").fetchone()[0] == 2


def test_every_existing_row_gets_its_own_identity(tmp_path):
    path = tmp_path / "toolkit.db"
    _legacy_journal(path)

    JournalRepository(path)

    with sqlite3.connect(path) as connection:
        for table in ("trades", "tracked_trades"):
            total, named, distinct = connection.execute(
                f"SELECT COUNT(*), COUNT(sync_uid), COUNT(DISTINCT sync_uid) FROM {table}"
            ).fetchone()
            assert named == total, f"{table}: a row was left unnamed"
            assert distinct == total, f"{table}: two rows share an identity"


def test_updated_at_starts_from_when_the_row_happened(tmp_path):
    """Not "now". Stamping every existing row with the migration time would make all of them
    look freshly edited and win every conflict on first contact with the other side."""
    path = tmp_path / "toolkit.db"
    _legacy_journal(path)

    JournalRepository(path)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM trades WHERE updated_at != recorded_at"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tracked_trades WHERE updated_at != created_at"
            ).fetchone()[0]
            == 0
        )


def test_nothing_is_deleted_by_default(tmp_path):
    path = tmp_path / "toolkit.db"
    _legacy_journal(path)

    JournalRepository(path)

    with sqlite3.connect(path) as connection:
        for table in ("trades", "tracked_trades"):
            assert (
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NOT NULL"
                ).fetchone()[0]
                == 0
            )


def test_reopening_does_not_re_mint_identities(tmp_path):
    """A migration that ran already is not one anybody needs run again -- and re-minting would
    make every row look like a brand new one to the other side."""
    path = tmp_path / "toolkit.db"
    _legacy_journal(path)

    JournalRepository(path)
    with sqlite3.connect(path) as connection:
        first = connection.execute(
            "SELECT position_id, sync_uid, updated_at FROM tracked_trades ORDER BY position_id"
        ).fetchall()

    JournalRepository(path)
    with sqlite3.connect(path) as connection:
        second = connection.execute(
            "SELECT position_id, sync_uid, updated_at FROM tracked_trades ORDER BY position_id"
        ).fetchall()

    assert first == second


def test_a_fresh_journal_has_the_columns_from_the_start(tmp_path):
    path = tmp_path / "toolkit.db"

    JournalRepository(path)

    assert SYNC_COLUMNS <= _columns(path, "trades")
    assert SYNC_COLUMNS <= _columns(path, "tracked_trades")


def test_a_newly_recorded_trade_is_named_at_birth(tmp_path):
    """A trade recorded with no network has to already own the identity it will later be
    recognised by -- waiting until first sync would leave it unsyncable until then."""
    path = tmp_path / "toolkit.db"
    repository = JournalRepository(path)
    repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    repository.add("Dragon bones", 500, 2_000, 2_600)

    with sqlite3.connect(path) as connection:
        total, named, distinct = connection.execute(
            "SELECT COUNT(*), COUNT(sync_uid), COUNT(DISTINCT sync_uid) FROM trades"
        ).fetchone()
    assert (total, named, distinct) == (2, 2, 2)


def test_a_newly_tracked_position_is_named_at_birth(tmp_path):
    path = tmp_path / "toolkit.db"
    repository = JournalRepository(path)
    repository.track(
        item_id=4151, item_name="Abyssal whip", quantity=1, target_buy=1, target_sell=2
    )

    with sqlite3.connect(path) as connection:
        uid, updated_at, created_at = connection.execute(
            "SELECT sync_uid, updated_at, created_at FROM tracked_trades"
        ).fetchone()
    assert uid
    assert updated_at == created_at


def test_two_rows_cannot_share_an_identity(tmp_path):
    """The unique index is what stops a bad merge quietly collapsing two trades into one."""
    path = tmp_path / "toolkit.db"
    repository = JournalRepository(path)
    repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    repository.add("Dragon bones", 500, 2_000, 2_600)

    with sqlite3.connect(path) as connection:
        first, second = [
            row[0] for row in connection.execute("SELECT sync_uid FROM trades ORDER BY trade_id")
        ]
        assert first and second and first != second
        try:
            connection.execute("UPDATE trades SET sync_uid = ? WHERE sync_uid = ?", (first, second))
        except sqlite3.IntegrityError:
            return
    raise AssertionError("the unique index did not stop two rows sharing an identity")
