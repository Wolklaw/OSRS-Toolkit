from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutItem, LoadoutSnapshot

from toolkit_web.config import Settings


def _snapshot() -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash="char-1",
        account_name="Wolklaw",
        captured_at=datetime.now(UTC).isoformat(),
        equipment=(),
        inventory=(),
        bank=(
            LoadoutItem(
                item_id=11802, item_name="Armadyl godsword", quantity=1, unit_value=40_000_000
            ),
            LoadoutItem(item_id=995, item_name="Coins", quantity=5_000_000, unit_value=1),
        ),
        skills={"Attack": 75, "Strength": 80},
    )


def test_the_loadout_page_shows_the_empty_state_with_no_snapshot(signed_in: TestClient):
    response = signed_in.get("/loadout")

    assert response.status_code == 200
    assert "No snapshot yet" in response.text


def test_the_loadout_page_shows_skills_and_bank(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(_snapshot())

    response = signed_in.get("/loadout")

    assert response.status_code == 200
    assert "Attack" in response.text
    assert "155" in response.text  # total level
    assert "Armadyl godsword" in response.text


def test_the_loadout_page_needs_a_session(client: TestClient):
    assert client.get("/loadout").status_code == 401
