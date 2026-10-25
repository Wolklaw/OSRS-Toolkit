from __future__ import annotations

import json
import uuid
from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter

ACCOUNT = "123456789"


def _write(root: Path, event_type: str, payload: dict[str, object], at: str) -> None:
    events = root / "events"
    events.mkdir(parents=True, exist_ok=True)
    event_id = str(uuid.uuid4())
    (events / f"{event_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": at,
                "account": {"hash": ACCOUNT, "name": "Example Player"},
                "payload": payload,
            }
        ),
        encoding="utf-8",
    )


def _progress(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "quest_points": 12,
        "quests": {"Cook's Assistant": "finished", "Dragon Slayer I": "in_progress"},
        "diaries": {"Ardougne": 2, "Varrock": 0},
        "combat_achievements": {"points": 41, "tiers": {"easy": 2, "medium": 1}},
        "slayer": {
            "task_id": 7,
            "task_name": "ABYSSAL DEMONS",
            "remaining": 120,
            "area": 0,
            "master": 8,
            "points": 410,
            "tasks_completed": 77,
        },
    }
    payload.update(overrides)
    return payload


def _import(tmp_path: Path):
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    result = RuneLiteSyncImporter(root).import_pending(repository)
    return repository, result


def test_progress_snapshot_is_stored_and_finished_quests_are_marked_complete(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "sync", "progress_snapshot", _progress(), "2026-10-25T10:00:00Z")

    repository, result = _import(tmp_path)

    assert result.imported == 1
    progress = repository.get_progress(ACCOUNT)
    assert progress is not None
    assert progress.quest_points == 12
    assert progress.diaries["Ardougne"] == 2
    assert progress.combat_points == 41
    assert progress.slayer["task_name"] == "ABYSSAL DEMONS"
    assert progress.slayer["remaining"] == 120
    assert repository.list_completed_quests(ACCOUNT) == {"Cook's Assistant"}


def test_an_older_progress_snapshot_does_not_replace_a_newer_one(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write(root, "progress_snapshot", _progress(quest_points=30), "2026-10-25T11:00:00Z")
    _write(root, "progress_snapshot", _progress(quest_points=12), "2026-10-25T10:00:00Z")

    repository, _result = _import(tmp_path)

    progress = repository.get_progress(ACCOUNT)
    assert progress is not None
    assert progress.quest_points == 30


def test_unknown_quest_state_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path / "sync",
        "progress_snapshot",
        _progress(quests={"Cook's Assistant": "done"}),
        "2026-10-25T10:00:00Z",
    )

    repository, result = _import(tmp_path)

    assert result.rejected == 1
    assert repository.get_progress(ACCOUNT) is None


def test_skills_snapshot_extends_history_and_refreshes_the_loadout_levels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    _write(
        root,
        "loadout_snapshot",
        {
            "equipment": [],
            "inventory": [],
            "bank": [],
            "skills": {"Attack": 70},
            "xp": {"Attack": 737_627},
        },
        "2026-10-25T09:00:00Z",
    )
    _write(
        root,
        "skills_snapshot",
        {"skills": {"Attack": 71}, "xp": {"Attack": 814_445}},
        "2026-10-25T10:00:00Z",
    )

    repository, result = _import(tmp_path)

    assert result.imported == 2
    history = repository.list_skills_history(ACCOUNT)
    assert [point.xp["Attack"] for point in history] == [737_627, 814_445]
    snapshot = repository.get_loadout_snapshot(ACCOUNT)
    assert snapshot is not None
    assert snapshot.skills["Attack"] == 71


def test_container_snapshot_is_stored_per_container(tmp_path: Path) -> None:
    _write(
        tmp_path / "sync",
        "container_snapshot",
        {
            "containers": {
                "looting_bag": [
                    {"item_id": 1, "item_name": "Pot", "quantity": 2, "unit_value": 100}
                ],
                "seed_vault": [],
            },
            "seen": ["looting_bag", "seed_vault"],
        },
        "2026-10-25T10:00:00Z",
    )

    repository, result = _import(tmp_path)

    assert result.imported == 1
    stored = repository.list_container_items(ACCOUNT)
    assert set(stored) == {"looting_bag", "seed_vault"}
    assert stored["looting_bag"][0].total_value == 200
    assert stored["seed_vault"] == ()


def test_event_loot_lands_beside_npc_loot_with_its_source_type(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write(
        root,
        "event_loot",
        {
            "source_name": "Chambers of Xeric",
            "source_type": "event",
            "combat_level": 0,
            "amount": 1,
            "items": [{"item_id": 5, "item_name": "Hide", "quantity": 1, "unit_value": 5_000}],
        },
        "2026-10-25T10:00:00Z",
    )
    _write(
        root,
        "npc_loot",
        {
            "npc_name": "Vorkath",
            "items": [{"item_id": 6, "item_name": "Bone", "quantity": 1, "unit_value": 9}],
        },
        "2026-10-25T10:05:00Z",
    )

    repository, result = _import(tmp_path)

    assert result.imported == 2
    kinds = {record.npc_name: record.source_type for record in repository.list_npc_loot_events()}
    assert kinds == {"Chambers of Xeric": "event", "Vorkath": "npc"}


def test_event_loot_with_an_unknown_source_type_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path / "sync",
        "event_loot",
        {
            "source_name": "Someone",
            "source_type": "player",
            "items": [{"item_id": 5, "item_name": "Hide", "quantity": 1, "unit_value": 5}],
        },
        "2026-10-25T10:00:00Z",
    )

    repository, result = _import(tmp_path)

    assert result.rejected == 1
    assert repository.list_npc_loot_events() == []
