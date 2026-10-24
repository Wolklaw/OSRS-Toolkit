from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutItem, NpcLootRecord, PlayerDeathRecord

from toolkit_web.config import Settings


def _loot_event(event_id: str = "loot-1", occurred_at: str | None = None) -> NpcLootRecord:
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=occurred_at or datetime.now(UTC).isoformat(),
        account_hash="abc123",
        account_name="Wolklaw",
        npc_name="Vorkath",
        items=(
            LoadoutItem(item_id=12934, item_name="Draconic visage", quantity=1, unit_value=100_000),
        ),
    )


def _death_event(event_id: str = "death-1", skulled: bool = True) -> PlayerDeathRecord:
    return PlayerDeathRecord(
        event_id=event_id,
        occurred_at=datetime.now(UTC).isoformat(),
        account_hash="abc123",
        account_name="Wolklaw",
        skulled=skulled,
        equipment=(
            LoadoutItem(item_id=4151, item_name="Abyssal whip", quantity=1, unit_value=1_500_000),
        ),
        inventory=(),
    )


def test_the_loot_log_page_shows_a_loot_event(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_npc_loot_event(_loot_event())

    response = signed_in.get("/loot-log")

    assert response.status_code == 200
    assert "Vorkath" in response.text
    assert "Draconic visage" in response.text


def test_the_loot_log_page_shows_a_per_npc_rollup(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    now = datetime.now(UTC)
    repository.add_npc_loot_event(_loot_event("loot-1", occurred_at=now.isoformat()))
    repository.add_npc_loot_event(
        _loot_event("loot-2", occurred_at=(now + timedelta(hours=1)).isoformat())
    )

    response = signed_in.get("/loot-log")

    assert response.status_code == 200
    assert "By NPC" in response.text
    assert "200,000" in response.text


def test_the_loot_log_page_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/loot-log")

    assert "Nothing recorded yet" in response.text


def test_the_loot_log_page_needs_a_session(client: TestClient):
    assert client.get("/loot-log").status_code == 401


def test_an_imported_loot_event_can_be_deleted(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_npc_loot_event(_loot_event())

    response = signed_in.delete("/api/loot-log/loot-1")

    assert response.status_code == 200
    assert "Vorkath" not in signed_in.get("/loot-log").text


def test_deleting_a_loot_event_needs_a_session(client: TestClient):
    assert client.delete("/api/loot-log/loot-1").status_code == 401


def test_the_death_log_page_shows_a_death_event(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_player_death_event(_death_event())

    response = signed_in.get("/death-log")

    assert response.status_code == 200
    assert "Abyssal whip" in response.text
    assert "Skulled" in response.text


def test_the_death_log_page_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/death-log")

    assert "Nothing recorded yet" in response.text


def test_the_death_log_page_needs_a_session(client: TestClient):
    assert client.get("/death-log").status_code == 401


def test_an_imported_death_event_can_be_deleted(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_player_death_event(_death_event())

    response = signed_in.delete("/api/death-log/death-1")

    assert response.status_code == 200
    assert "Abyssal whip" not in signed_in.get("/death-log").text


def test_deleting_a_death_event_needs_a_session(client: TestClient):
    assert client.delete("/api/death-log/death-1").status_code == 401
