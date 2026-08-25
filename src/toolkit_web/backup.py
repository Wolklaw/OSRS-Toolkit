"""Take a consistent copy of everything here that could not be rebuilt.

    python -m toolkit_web.backup

Runs from a timer on the machine hosting the site. Until other people had accounts this was a
convenience; it is not one any more, because the journals under ``data_dir`` are now somebody
else's trade history and the only copy of it is one laptop's disk.

**Copied through SQLite's own backup API, not by copying the file.** These databases run in WAL
mode with the services still writing to them, so the file on disk is only half the story --
the newest rows live in a ``-wal`` sidecar until a checkpoint folds them in. ``cp`` at the
wrong moment gets a torn database that restores missing whatever arrived most recently, and
looks perfectly fine until someone goes looking for it. ``Connection.backup`` takes a
transactionally consistent snapshot of a live database instead, which is the whole reason this
is a script rather than a line in a tar command.

The market cache is deliberately left out. It is a copy of the wiki's prices and rebuilds
itself within a minute of being missing.
"""

from __future__ import annotations

import os
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

#: How many archives to keep. Daily from a timer, so this is two weeks of history -- long
#: enough that a problem noticed on a Monday can still be recovered from the week before.
DEFAULT_KEEP = 14


def snapshot_sqlite(source: Path, destination: Path) -> None:
    """Copy a live SQLite database to ``destination``, WAL and all.

    Opened read-only: a backup has no business creating the file it was pointed at, and a
    typo in a path should fail here rather than quietly produce an empty database that
    restores over the real one.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(destination)) as copy:
            live.backup(copy)


def stage(data_dir: Path, sync_db: Path | None, staging: Path) -> list[Path]:
    """Lay out one backup's worth of files under ``staging``. Returns what was copied.

    Missing sources are skipped rather than fatal. A machine that has never run the sync
    service still has journals worth keeping, and refusing to back those up because an
    unrelated database is absent would be the wrong trade.
    """
    copied = []

    accounts = data_dir / "accounts.db"
    if accounts.exists():
        snapshot_sqlite(accounts, staging / "accounts.db")
        copied.append(accounts)

    # One database per account, under a directory named for the account id.
    for journal in sorted((data_dir / "journals").glob("*/toolkit.db")):
        snapshot_sqlite(journal, staging / "journals" / journal.parent.name / "toolkit.db")
        copied.append(journal)

    if sync_db is not None and sync_db.exists():
        snapshot_sqlite(sync_db, staging / "sync.db")
        copied.append(sync_db)

    return copied


def prune_old(backup_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` archives. Returns what went.

    Sorted by name rather than by mtime: the names carry a UTC timestamp in a format that
    sorts chronologically as text, and mtime is the thing that changes if the directory is
    ever copied around.
    """
    archives = sorted(backup_dir.glob("runescope-*.tar.gz"))
    doomed = archives[:-keep] if keep > 0 else archives
    for archive in doomed:
        archive.unlink()
    return doomed


def run(data_dir: Path, backup_dir: Path, sync_db: Path | None, keep: int) -> Path | None:
    """Write one archive and drop the ones that have aged out. None where nothing was found.

    Staged into a temporary directory and tarred from there, so a backup that fails halfway
    leaves no half-written archive behind for the pruner to count as one of its ``keep``.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive = backup_dir / f"runescope-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        copied = stage(data_dir, sync_db, staging)
        if not copied:
            return None
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=str(path.relative_to(staging)))

    prune_old(backup_dir, keep)
    return archive


def main() -> None:
    data_dir = Path(os.environ.get("TOOLKIT_DATA_DIR", "data")).expanduser()
    backup_dir = Path(os.environ.get("TOOLKIT_BACKUP_DIR", "~/backups")).expanduser()
    raw_sync = os.environ.get("TOOLKIT_BACKUP_SYNC_DB", "~/.local/share/osrs-sync/sync.db")
    sync_db = Path(raw_sync).expanduser() if raw_sync.strip() else None
    keep = int(os.environ.get("TOOLKIT_BACKUP_KEEP", DEFAULT_KEEP))

    archive = run(data_dir, backup_dir, sync_db, keep)
    # Printed rather than logged: this runs from a timer, so stdout is the journal entry that
    # says whether last night's backup happened and how big it was.
    if archive is None:
        print(f"Nothing to back up under {data_dir}")
        return
    print(f"Wrote {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
