from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import (
    ContainerSnapshot,
    JournalRepository,
    LoadoutItem,
    ProgressRecord,
)

from toolkit_web.config import Settings


def _record() -> ProgressRecord:
    return ProgressRecord(
        account_hash="abc123",
        account_name="Wolklaw",
        captured_at="2026-10-25T10:00:00+00:00",
        quest_points=212,
        quests={"Cook's Assistant": "finished", "Dragon Slayer II": "in_progress"},
        diaries={"Ardougne": 3, "Varrock": 4, "Desert": 0},
        combat_points=500,
        combat_tiers={"easy": 2},
        slayer={
            "task_id": 7,
            "task_name": "ABYSSAL DEMONS",
            "remaining": 87,
            "area": 0,
            "master": 8,
            "points": 410,
            "tasks_completed": 77,
        },
    )


def test_the_achievements_page_reads_what_the_plugin_sent(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_progress(_record())
    repository.save_container_snapshot(
        ContainerSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at="2026-10-25T10:00:00+00:00",
            containers={"seed_vault": (LoadoutItem(1, "Ranarr seed", 10, 5_000),)},
        )
    )
    signed_in.cookies.set("active_character", "abc123")

    page = signed_in.get("/achievements").text

    assert "212" in page
    assert "7<span" in page
    assert "Abyssal Demons" in page
    assert "Hard tier reached" in page
    assert "Seed vault" in page
    assert "50,000 gp" in page


def test_the_achievements_page_says_how_to_switch_tracking_on(
    signed_in: TestClient,
):
    signed_in.cookies.set("active_character", "abc123")

    assert "Track quests, diaries and Slayer" in signed_in.get("/achievements").text


def test_the_achievements_page_needs_a_character(signed_in: TestClient):
    assert "No character selected" in signed_in.get("/achievements").text
