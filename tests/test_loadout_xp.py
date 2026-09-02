"""Skill experience alongside skill levels in loadout snapshots.

A level alone can't say whether a session did anything -- level 99 covers a 13M-xp range.
The plugin now sends an optional ``xp`` map beside ``skills``; these cover it parsing, an
older plugin build that still doesn't send it, and a journal that predates the column.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter


def _event(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": "9ca987d2-bcd1-45ea-8320-b17a956e38c9",
        "event_type": "loadout_snapshot",
        "occurred_at": "2026-08-11T05:00:00Z",
        "account": {"hash": "123456789", "name": "Example Player"},
        "payload": payload,
    }


def _write_event(root: Path, event: dict[str, object]) -> None:
    events = root / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / f"{event['event_id']}.json").write_text(json.dumps(event), encoding="utf-8")


def _base_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "equipment": [],
        "inventory": [],
        "bank": [],
        "skills": {"Attack": 75, "Strength": 80},
    }
    payload.update(overrides)
    return payload


def test_a_snapshot_with_xp_carries_it_through_to_the_journal(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    payload = _base_payload(xp={"Attack": 1_210_421, "Strength": 1_986_068})
    _write_event(root, _event(payload))

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    snapshot = repository.get_loadout_snapshot("123456789")
    assert snapshot is not None
    assert snapshot.xp == {"Attack": 1_210_421, "Strength": 1_986_068}
    assert snapshot.skills == {"Attack": 75, "Strength": 80}

    history = repository.list_skills_history("123456789")
    assert len(history) == 1
    assert history[0].xp == {"Attack": 1_210_421, "Strength": 1_986_068}


def test_a_snapshot_from_a_plugin_build_that_predates_xp_still_parses(tmp_path: Path) -> None:
    """No ``xp`` key at all -- not ``null``, not ``{}`` -- is what an older plugin build sends."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    _write_event(root, _event(_base_payload()))

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    snapshot = repository.get_loadout_snapshot("123456789")
    assert snapshot is not None
    assert snapshot.xp == {}
    assert snapshot.skills == {"Attack": 75, "Strength": 80}


def test_the_xp_column_is_added_to_a_journal_that_predates_it(tmp_path: Path) -> None:
    """A journal written before this migration existed has ``loadout_snapshots`` and
    ``skills_history`` without ``xp_json`` at all -- opening it must add the column and
    leave the existing row readable, defaulting to no recorded experience."""
    db_path = tmp_path / "journal.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE loadout_snapshots (account_hash TEXT PRIMARY KEY,"
            " account_name TEXT NOT NULL, captured_at TEXT NOT NULL,"
            " equipment_json TEXT NOT NULL, inventory_json TEXT NOT NULL,"
            " bank_json TEXT NOT NULL, skills_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE skills_history (entry_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " account_hash TEXT NOT NULL, account_name TEXT NOT NULL,"
            " captured_at TEXT NOT NULL, skills_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO loadout_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "123456789",
                "Example Player",
                "2026-08-01T00:00:00Z",
                "[]",
                "[]",
                "[]",
                '{"Attack": 70}',
            ),
        )
        connection.execute(
            "INSERT INTO skills_history (account_hash, account_name, captured_at, skills_json)"
            " VALUES (?, ?, ?, ?)",
            ("123456789", "Example Player", "2026-08-01T00:00:00Z", '{"Attack": 70}'),
        )

    repository = JournalRepository(db_path)

    snapshot = repository.get_loadout_snapshot("123456789")
    assert snapshot is not None
    assert snapshot.skills == {"Attack": 70}
    assert snapshot.xp == {}

    history = repository.list_skills_history("123456789")
    assert len(history) == 1
    assert history[0].xp == {}

    with sqlite3.connect(db_path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(loadout_snapshots)")
        }
    assert "xp_json" in columns


def test_downgrading_the_plugin_after_it_has_sent_xp_does_not_break_the_session_page(
    tmp_path: Path,
) -> None:
    """The real scenario this guards: someone tries the build that sends ``xp``, then goes
    back to one that doesn't. The later, xp-less snapshot becomes the newest one on file --
    ``session_summary`` (see ``JournalRepository.session_summary``) always diffs against the
    newest snapshot, so it must not crash, and it must fall back to levels rather than report
    a false zero as if the earlier xp gain never happened."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    importer = RuneLiteSyncImporter(root)

    # 1.0.3-style: a loadout snapshot with xp, then a kill.
    _write_event(
        root,
        {
            "schema_version": 1,
            "event_id": "11111111-1111-1111-1111-111111111111",
            "event_type": "loadout_snapshot",
            "occurred_at": "2026-08-25T17:00:00Z",
            "account": {"hash": "123456789", "name": "Example Player"},
            "payload": _base_payload(xp={"Attack": 1_210_421, "Strength": 1_986_068}),
        },
    )
    importer.import_pending(repository)
    _write_event(
        root,
        {
            "schema_version": 1,
            "event_id": "22222222-2222-2222-2222-222222222222",
            "event_type": "npc_loot",
            "occurred_at": "2026-08-25T18:00:00Z",
            "account": {"hash": "123456789", "name": "Example Player"},
            "payload": {
                "npc_name": "Vorkath",
                "items": [
                    {"item_id": 995, "item_name": "Coins", "quantity": 300_000, "unit_value": 1}
                ],
            },
        },
    )
    importer.import_pending(repository)

    # 1.0.2-style: downgraded, no "xp" key at all -- the next snapshot after the kill.
    _write_event(
        root,
        {
            "schema_version": 1,
            "event_id": "33333333-3333-3333-3333-333333333333",
            "event_type": "loadout_snapshot",
            "occurred_at": "2026-08-25T18:30:00Z",
            "account": {"hash": "123456789", "name": "Example Player"},
            "payload": _base_payload(skills={"Attack": 76, "Strength": 80}),
        },
    )
    importer.import_pending(repository)

    summary = repository.session_summary("123456789")

    assert summary is not None
    assert summary.loot_value == 300_000
    # The newest snapshot (post-downgrade) has no xp, so there is nothing to diff -- this
    # must read as "no experience recorded", not raise, and not report a stale number.
    assert summary.xp_gained == {}
    # Levels still moved (75 -> 76 on Attack) and that's still worth showing.
    assert summary.levels_gained == {"Attack": 1}
