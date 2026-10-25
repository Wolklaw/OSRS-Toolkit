from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutSnapshot

from toolkit_web.config import Settings


def _sync_levels(settings: Settings, **levels: int) -> JournalRepository:
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(),
            bank=(),
            skills=levels,
        )
    )
    return repository


def test_the_quest_page_needs_a_session(client: TestClient):
    assert client.get("/quests", follow_redirects=False).status_code in (302, 303, 307, 401)


def test_the_page_asks_for_a_sync_when_there_is_no_character(signed_in: TestClient):
    page = signed_in.get("/quests")

    assert page.status_code == 200
    assert "No character yet" in page.text


def test_the_guide_suggests_quests_for_a_synced_character(
    signed_in: TestClient, settings: Settings
):
    _sync_levels(settings, Magic=75, Smithing=70)

    page = signed_in.get("/quests").text

    assert "Do next" in page
    assert "Cook&#39;s Assistant" in page or "Cook's Assistant" in page


def test_ticking_a_quest_is_remembered_and_brings_what_it_needed(
    signed_in: TestClient, settings: Settings
):
    repository = _sync_levels(settings, Magic=99)

    response = signed_in.post(
        "/api/quests", json={"quest_names": ["Legends' Quest"], "completed": True}
    )

    assert response.status_code == 200
    done = repository.list_completed_quests("abc123")
    assert {"Legends' Quest", "Heroes' Quest"} <= done


def test_unticking_removes_the_quests_that_depended_on_it(
    signed_in: TestClient, settings: Settings
):
    repository = _sync_levels(settings, Magic=99)
    signed_in.post("/api/quests", json={"quest_names": ["Legends' Quest"], "completed": True})

    signed_in.post("/api/quests", json={"quest_names": ["Heroes' Quest"], "completed": False})

    done = repository.list_completed_quests("abc123")
    assert "Legends' Quest" not in done
    assert "Heroes' Quest" not in done


def test_unknown_quests_and_bad_payloads_are_refused(signed_in: TestClient, settings: Settings):
    _sync_levels(settings, Magic=99)

    assert (
        signed_in.post(
            "/api/quests", json={"quest_names": ["Made Up Quest"], "completed": True}
        ).status_code
        == 422
    )
    assert (
        signed_in.post(
            "/api/quests", json={"quest_names": ["Demon Slayer"], "completed": "yes"}
        ).status_code
        == 422
    )


def test_ticking_without_any_character_is_a_clear_conflict(signed_in: TestClient):
    response = signed_in.post(
        "/api/quests", json={"quest_names": ["Demon Slayer"], "completed": True}
    )

    assert response.status_code == 409


def test_the_skill_goals_page_shows_xp_to_99_and_levels_that_open_quests(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(),
            bank=(),
            skills={"Slayer": 70, "Cooking": 1},
            xp={"Slayer": 750_000, "Cooking": 0},
        )
    )

    page = signed_in.get("/skill-goals")

    assert page.status_code == 200
    assert "Slayer" in page.text
    assert "12,284,431" in page.text
    assert "Levels that open quests" in page.text


def test_the_skill_goals_page_asks_for_a_sync_when_there_are_no_levels(signed_in: TestClient):
    assert "No levels yet" in signed_in.get("/skill-goals").text
