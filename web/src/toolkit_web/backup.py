from __future__ import annotations

import os
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_KEEP = 14


def snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live,
        closing(sqlite3.connect(destination)) as copy,
    ):
        live.backup(copy)


def stage(data_dir: Path, sync_db: Path | None, staging: Path) -> list[Path]:
    copied = []

    accounts = data_dir / "accounts.db"
    if accounts.exists():
        snapshot_sqlite(accounts, staging / "accounts.db")
        copied.append(accounts)

    for journal in sorted((data_dir / "journals").glob("*/toolkit.db")):
        snapshot_sqlite(journal, staging / "journals" / journal.parent.name / "toolkit.db")
        copied.append(journal)

    if sync_db is not None and sync_db.exists():
        snapshot_sqlite(sync_db, staging / "sync.db")
        copied.append(sync_db)

    return copied


def prune_old(backup_dir: Path, keep: int) -> list[Path]:
    archives = sorted(backup_dir.glob("runescope-*.tar.gz"))
    doomed = archives[:-keep] if keep > 0 else archives
    for archive in doomed:
        archive.unlink()
    return doomed


def run(data_dir: Path, backup_dir: Path, sync_db: Path | None, keep: int) -> Path | None:
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
    if archive is None:
        print(f"Nothing to back up under {data_dir}")
        return
    print(f"Wrote {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
