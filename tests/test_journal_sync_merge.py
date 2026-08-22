"""Exporting journal rows, merging them back, and who wins when both sides changed one.

These run two real journals against each other rather than mocking a transport, because the
thing worth testing is the merge, and the merge is the same code whether the rows crossed a
network or a function call.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from osrs_toolkit.journal import JournalRepository


def _repo(tmp_path: Path, name: str) -> JournalRepository:
    return JournalRepository(tmp_path / f"{name}.db")


def _uid_of(repository: JournalRepository, table: str) -> str:
    with sqlite3.connect(repository.database_path) as connection:
        return str(connection.execute(f"SELECT sync_uid FROM {table}").fetchone()[0])


def _set_stamp(repository: JournalRepository, table: str, uid: str, stamp: str) -> None:
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(f"UPDATE {table} SET updated_at = ? WHERE sync_uid = ?", (stamp, uid))


# -- version -------------------------------------------------------------------------------


def test_version_moves_when_something_changes(tmp_path):
    repository = _repo(tmp_path, "a")
    first = repository.sync_version()
    assert first["counts"]["trades"] == 0

    repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)

    second = repository.sync_version()
    assert second["version"] > first["version"]
    assert second["counts"]["trades"] == 1


def test_version_is_steady_when_nothing_happens(tmp_path):
    """This is what a client polls on a timer — it must not look like news every time."""
    repository = _repo(tmp_path, "a")
    repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    assert repository.sync_version() == repository.sync_version()


# -- seeding an empty side ------------------------------------------------------------------


def test_a_full_export_seeds_an_empty_journal(tmp_path):
    """The first sync: one side has a month of history, the other has nothing."""
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    source.track(item_id=4151, item_name="Abyssal whip", quantity=1, target_buy=1, target_sell=2)

    destination = _repo(tmp_path, "website")
    applied = destination.sync_apply(source.sync_export())

    assert applied["inserted"] == 2
    assert [trade.item_name for trade in destination.list_all()] == ["Abyssal whip"]
    assert [position.item_name for position in destination.list_tracked()] == ["Abyssal whip"]


def test_seeding_twice_does_not_duplicate(tmp_path):
    """A retried sync, or a second client, must not double the journal."""
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination = _repo(tmp_path, "website")

    destination.sync_apply(source.sync_export())
    second = destination.sync_apply(source.sync_export())

    assert second["inserted"] == 0
    assert len(destination.list_all()) == 1


# -- who wins ------------------------------------------------------------------------------


def test_the_newer_edit_wins(tmp_path):
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())

    uid = _uid_of(source, "trades")
    _set_stamp(source, "trades", uid, "2030-01-01T00:00:00+00:00")
    with sqlite3.connect(source.database_path) as connection:
        connection.execute("UPDATE trades SET sell_price = 9 WHERE sync_uid = ?", (uid,))

    applied = destination.sync_apply(source.sync_export())

    assert applied["updated"] == 1
    assert destination.list_all()[0].sell_price == 9


def test_a_stale_row_cannot_walk_the_journal_backwards(tmp_path):
    """A slow client replaying old state must not undo a newer edit."""
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())

    uid = _uid_of(destination, "trades")
    _set_stamp(destination, "trades", uid, "2030-01-01T00:00:00+00:00")
    _set_stamp(source, "trades", uid, "2020-01-01T00:00:00+00:00")
    with sqlite3.connect(source.database_path) as connection:
        connection.execute("UPDATE trades SET sell_price = 1 WHERE sync_uid = ?", (uid,))

    applied = destination.sync_apply(source.sync_export())

    assert applied["skipped"] == 1
    assert destination.list_all()[0].sell_price == 1_650_000


def test_offsets_and_zulu_compare_as_the_same_instant(tmp_path):
    """The two sides stamp their own rows. "+00:00" against "Z" orders wrongly as text."""
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())

    uid = _uid_of(source, "trades")
    _set_stamp(destination, "trades", uid, "2026-08-22T12:00:00+00:00")
    with sqlite3.connect(source.database_path) as connection:
        connection.execute("UPDATE trades SET sell_price = 7 WHERE sync_uid = ?", (uid,))
    # Stamped after the edit on purpose: the touch trigger would otherwise overwrite this
    # with the real clock and the test would stop being about how the two spellings compare.
    _set_stamp(source, "trades", uid, "2026-08-22T11:00:00Z")

    applied = destination.sync_apply(source.sync_export())

    assert applied["skipped"] == 1, "an hour earlier is earlier, whichever way it is spelt"
    assert destination.list_all()[0].sell_price == 1_650_000


# -- deletions -----------------------------------------------------------------------------


def test_a_deletion_travels(tmp_path):
    source = _repo(tmp_path, "desktop")
    trade_id = source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())
    assert len(destination.list_all()) == 1

    source.delete(trade_id)
    destination.sync_apply(source.sync_export())

    assert destination.list_all() == []


def test_a_deleted_row_does_not_come_back_on_the_next_pull(tmp_path):
    """The reason deletions are tombstones rather than removals: a row that is simply gone
    reads as "never seen" to the other side, which would re-send it forever."""
    source = _repo(tmp_path, "desktop")
    destination = _repo(tmp_path, "website")
    trade_id = source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    destination.sync_apply(source.sync_export())
    source.delete(trade_id)
    destination.sync_apply(source.sync_export())

    # the deleting side pulls back from the other, repeatedly
    for _ in range(3):
        source.sync_apply(destination.sync_export())
        destination.sync_apply(source.sync_export())

    assert source.list_all() == []
    assert destination.list_all() == []


# -- incremental ---------------------------------------------------------------------------


def test_since_only_carries_what_changed(tmp_path):
    repository = _repo(tmp_path, "a")
    repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    watermark = repository.sync_version()["version"]

    assert repository.sync_export(since=watermark)["trades"] == []

    repository.add("Dragon bones", 500, 2_000, 2_600)
    changed = repository.sync_export(since=watermark)["trades"]
    assert [row["item_name"] for row in changed] == ["Dragon bones"]


# -- positions carry their fills ------------------------------------------------------------


def test_a_position_carries_its_fills(tmp_path):
    source = _repo(tmp_path, "desktop")
    position_id = source.track(
        item_id=4151, item_name="Abyssal whip", quantity=10, target_buy=100, target_sell=200
    )
    with sqlite3.connect(source.database_path) as connection:
        connection.executemany(
            "INSERT INTO tracked_sale_fills (position_id, quantity, sell_price) VALUES (?, ?, ?)",
            [(position_id, 4, 210), (position_id, 6, 190)],
        )

    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())

    with sqlite3.connect(destination.database_path) as connection:
        fills = connection.execute(
            "SELECT quantity, sell_price FROM tracked_sale_fills ORDER BY quantity"
        ).fetchall()
    assert [tuple(fill) for fill in fills] == [(4, 210), (6, 190)]


def test_fills_are_replaced_not_accumulated(tmp_path):
    """Applying the same position twice must not stack its fills up."""
    source = _repo(tmp_path, "desktop")
    position_id = source.track(
        item_id=4151, item_name="Abyssal whip", quantity=10, target_buy=100, target_sell=200
    )
    with sqlite3.connect(source.database_path) as connection:
        connection.execute(
            "INSERT INTO tracked_sale_fills (position_id, quantity, sell_price) VALUES (?, ?, ?)",
            (position_id, 10, 210),
        )
    destination = _repo(tmp_path, "website")
    destination.sync_apply(source.sync_export())

    uid = _uid_of(source, "tracked_trades")
    _set_stamp(source, "tracked_trades", uid, "2030-01-01T00:00:00+00:00")
    destination.sync_apply(source.sync_export())

    with sqlite3.connect(destination.database_path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM tracked_sale_fills").fetchone()[0]
    assert total == 1


# -- tolerance -----------------------------------------------------------------------------


def test_a_column_the_other_side_has_never_heard_of_is_ignored(tmp_path):
    """A newer build must be able to talk to an older one without failing the whole sync."""
    source = _repo(tmp_path, "desktop")
    source.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    payload = source.sync_export()
    payload["trades"][0]["a_column_from_the_future"] = "surprise"

    destination = _repo(tmp_path, "website")
    applied = destination.sync_apply(payload)

    assert applied["inserted"] == 1
    assert destination.list_all()[0].item_name == "Abyssal whip"


def test_rubbish_rows_are_skipped_rather_than_fatal(tmp_path):
    destination = _repo(tmp_path, "website")
    applied = destination.sync_apply(
        {"trades": [None, "nonsense", {}, {"sync_uid": ""}], "tracked_trades": None}
    )
    assert applied["inserted"] == 0
    assert applied["skipped"] == 4
    assert destination.list_all() == []
