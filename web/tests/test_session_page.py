from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutItem, NpcLootRecord

from toolkit_web.config import Settings

ACCOUNT_HASH = "abc123"


def _loot_event(event_id: str = "loot-1", occurred_at: str | None = None) -> NpcLootRecord:
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=occurred_at or datetime.now(UTC).isoformat(),
        account_hash=ACCOUNT_HASH,
        account_name="Wolklaw",
        npc_name="Vorkath",
        items=(
            LoadoutItem(item_id=12934, item_name="Draconic visage", quantity=1, unit_value=100_000),
        ),
    )


def _with_active_character(client: TestClient) -> TestClient:
    """The switcher only fixes the fixture's account to one character over a real pairing
    token; tests fake the same choice directly via the cookie it ends up writing."""
    client.cookies.set("active_character", ACCOUNT_HASH)
    return client


def test_the_session_page_shows_the_most_recent_activity(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_npc_loot_event(_loot_event())

    response = _with_active_character(signed_in).get("/session")

    assert response.status_code == 200
    assert "Vorkath" in response.text
    assert "100,000" in response.text


def test_the_session_page_shows_the_empty_state_with_no_companion_events(
    signed_in: TestClient,
):
    response = _with_active_character(signed_in).get("/session")

    assert response.status_code == 200
    assert "Nothing tracked yet" in response.text


def test_the_session_page_asks_for_a_character_when_none_is_selected(
    signed_in: TestClient,
):
    response = signed_in.get("/session")

    assert response.status_code == 200
    assert "Pick a character" in response.text


def test_the_dashboard_shows_a_teaser_linking_to_the_full_session(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_npc_loot_event(_loot_event())

    response = _with_active_character(signed_in).get("/")

    assert response.status_code == 200
    assert "Last session" in response.text
    assert 'href="/session"' in response.text
