from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings

TOKEN = "plugin-pairing-token"


def _pair(client: TestClient) -> None:
    client.app.state.accounts.set_pairing_token("111", TOKEN)


def test_suggestions_need_a_pairing_token(signed_in: TestClient):
    assert signed_in.get("/api/plugin/ge-suggestions?account_hash=abc123").status_code == 401

    wrong = signed_in.get(
        "/api/plugin/ge-suggestions?account_hash=abc123",
        headers={"Authorization": "Bearer not-the-token"},
    )

    assert wrong.status_code == 401


def test_a_position_waiting_to_buy_is_suggested_with_its_plan(
    signed_in: TestClient, settings: Settings
):
    _pair(signed_in)
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(453, "Coal", 500, 150, 200, account_hash="abc123")

    response = signed_in.get(
        "/api/plugin/ge-suggestions?account_hash=abc123",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json()["suggestions"] == [
        {
            "item_id": 453,
            "item_name": "Coal",
            "side": "buy",
            "price": 150,
            "quantity": 500,
            "source": "position",
        }
    ]


def test_another_characters_positions_are_not_suggested(signed_in: TestClient, settings: Settings):
    _pair(signed_in)
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(453, "Coal", 500, 150, 200, account_hash="someone-else")

    response = signed_in.get(
        "/api/plugin/ge-suggestions?account_hash=abc123",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.json() == {"suggestions": []}
