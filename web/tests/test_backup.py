"""Backups of the journals, which stopped being only Wolklaw's data on 2026-08-25.

The one that matters is ``test_a_snapshot_includes_rows_still_in_the_wal``: these databases are
WAL-mode and live, so a plain file copy silently drops whatever has not been checkpointed yet
and still restores without complaint.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

from toolkit_web.backup import prune_old, run, snapshot_sqlite


def _make_db(path: Path, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE thing (id INTEGER PRIMARY KEY, label TEXT)")
        db.executemany("INSERT INTO thing (label) VALUES (?)", [(f"row-{n}",) for n in range(rows)])


def _names(archive: Path) -> set[str]:
    with tarfile.open(archive) as tar:
        return set(tar.getnames())


def test_a_snapshot_includes_rows_still_in_the_wal(tmp_path: Path):
    """A live WAL database keeps its newest rows in a sidecar until a checkpoint. Copying the
    .db file alone loses them, which is the failure this whole module exists to avoid."""
    source = tmp_path / "live.db"
    _make_db(source)
    # Held open and uncheckpointed, exactly as the running service leaves it.
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("INSERT INTO thing (label) VALUES ('only-in-wal')")
    connection.commit()

    snapshot_sqlite(source, tmp_path / "copy.db")

    with sqlite3.connect(tmp_path / "copy.db") as copy:
        labels = {row[0] for row in copy.execute("SELECT label FROM thing")}
    connection.close()
    assert "only-in-wal" in labels


def test_it_archives_the_accounts_db_and_every_journal(tmp_path: Path):
    data_dir = tmp_path / "data"
    _make_db(data_dir / "accounts.db")
    _make_db(data_dir / "journals" / "111" / "toolkit.db")
    _make_db(data_dir / "journals" / "222" / "toolkit.db")

    archive = run(data_dir, tmp_path / "backups", None, keep=14)

    assert archive is not None
    assert _names(archive) == {
        "accounts.db",
        "journals/111/toolkit.db",
        "journals/222/toolkit.db",
    }


def test_it_includes_the_sync_database_when_there_is_one(tmp_path: Path):
    data_dir = tmp_path / "data"
    _make_db(data_dir / "accounts.db")
    sync_db = tmp_path / "sync" / "sync.db"
    _make_db(sync_db)

    archive = run(data_dir, tmp_path / "backups", sync_db, keep=14)

    assert "sync.db" in _names(archive)


def test_a_missing_sync_database_is_skipped_rather_than_fatal(tmp_path: Path):
    """Journals are worth keeping whether or not an unrelated service is installed."""
    data_dir = tmp_path / "data"
    _make_db(data_dir / "accounts.db")

    archive = run(data_dir, tmp_path / "backups", tmp_path / "nope.db", keep=14)

    assert archive is not None
    assert "accounts.db" in _names(archive)


def test_it_leaves_the_market_cache_out(tmp_path: Path):
    """Regenerable from the wiki within a minute, and the largest thing in the directory."""
    data_dir = tmp_path / "data"
    _make_db(data_dir / "accounts.db")
    (data_dir / "market-cache").mkdir()
    (data_dir / "market-cache" / "prices.json").write_text("{}")

    archive = run(data_dir, tmp_path / "backups", None, keep=14)

    assert not any("market-cache" in name for name in _names(archive))


def test_nothing_to_back_up_writes_no_archive(tmp_path: Path):
    """An empty archive would still count towards `keep` and push a real one out."""
    backup_dir = tmp_path / "backups"

    assert run(tmp_path / "empty", backup_dir, None, keep=14) is None
    assert list(backup_dir.glob("*.tar.gz")) == []


def test_it_keeps_only_the_newest_archives(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for day in range(1, 8):
        (backup_dir / f"runescope-2026080{day}-000000.tar.gz").write_text("x")

    prune_old(backup_dir, keep=3)

    assert sorted(p.name for p in backup_dir.glob("*.tar.gz")) == [
        "runescope-20260805-000000.tar.gz",
        "runescope-20260806-000000.tar.gz",
        "runescope-20260807-000000.tar.gz",
    ]


def test_pruning_leaves_anything_that_is_not_one_of_ours_alone(tmp_path: Path):
    """That directory is a person's own folder, not this script's exclusive property."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "runescope-20260801-000000.tar.gz").write_text("x")
    (backup_dir / "runescope-20260802-000000.tar.gz").write_text("x")
    (backup_dir / "holiday-photos.tar.gz").write_text("x")

    prune_old(backup_dir, keep=1)

    assert (backup_dir / "holiday-photos.tar.gz").exists()


def test_a_restored_journal_still_reads(tmp_path: Path):
    """The point of all of it: what comes back out has to open."""
    data_dir = tmp_path / "data"
    _make_db(data_dir / "journals" / "111" / "toolkit.db", rows=5)

    archive = run(data_dir, tmp_path / "backups", None, keep=14)

    restored = tmp_path / "restored"
    with tarfile.open(archive) as tar:
        tar.extractall(restored, filter="data")
    with sqlite3.connect(restored / "journals" / "111" / "toolkit.db") as db:
        assert db.execute("SELECT COUNT(*) FROM thing").fetchone()[0] == 5
