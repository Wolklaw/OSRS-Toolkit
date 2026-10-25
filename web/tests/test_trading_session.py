from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def test_the_dashboard_says_buys_are_supplies_until_a_session_starts(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Not flipping" in response.text
    assert "Start trading session" in response.text


def test_starting_a_session_changes_what_the_dashboard_says(signed_in: TestClient):
    assert signed_in.post("/api/trading-session", json={}).status_code == 201

    response = signed_in.get("/")

    assert "Flipping" in response.text
    assert "Stop session" in response.text


def test_a_session_decides_whether_a_synced_buy_becomes_a_flip(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.apply_synced_ge_fill(561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000)

    signed_in.post("/api/trading-session", json={"label": "Evening"})
    repository.apply_synced_ge_fill(1_333, "Rune scimitar", "buy", 10, 15_000, total_quantity=10)

    statuses = {trade.item_name: trade.status for trade in repository.list_tracked()}
    assert statuses == {"Nature rune": "Supplies", "Rune scimitar": "Bought"}


def test_stopping_a_session_reports_what_it_closed(signed_in: TestClient):
    signed_in.post("/api/trading-session", json={})

    response = signed_in.request("DELETE", "/api/trading-session")

    assert response.status_code == 200
    assert response.json()["ended"]["ended_at"] is not None
    assert signed_in.get("/api/trading-session").json()["active"] is None


def test_stopping_a_session_that_is_not_running_says_so(signed_in: TestClient):
    response = signed_in.request("DELETE", "/api/trading-session")

    assert response.status_code == 200
    assert response.json() == {"ended": None}


def test_a_session_can_be_backdated_to_cover_buys_already_made(signed_in: TestClient):
    response = signed_in.post("/api/trading-session", json={"backdate_minutes": 60})

    assert response.status_code == 201
    started = response.json()["active"]["started_at"]
    now = signed_in.get("/api/trading-session").json()["active"]["started_at"]
    assert started == now


def test_a_nonsense_backdate_is_refused(signed_in: TestClient):
    response = signed_in.post("/api/trading-session", json={"backdate_minutes": "an hour"})

    assert response.status_code == 422


def test_the_tracking_mode_can_be_put_back_to_how_it_was(signed_in: TestClient, settings: Settings):
    assert signed_in.put("/api/flip-tracking-mode", json={"mode": "always"}).status_code == 200

    repository = JournalRepository(settings.journal_path("111"))
    repository.apply_synced_ge_fill(561, "Nature rune", "buy", 1_000, 95, total_quantity=1_000)

    assert repository.list_tracked()[0].status == "Bought"
    assert "Trading session" not in signed_in.get("/").text


def test_an_unknown_tracking_mode_is_refused(signed_in: TestClient):
    response = signed_in.put("/api/flip-tracking-mode", json={"mode": "sometimes"})

    assert response.status_code == 422


def test_the_session_api_needs_an_account(client: TestClient):
    assert client.get("/api/trading-session").status_code == 401
    assert client.post("/api/trading-session", json={}).status_code == 401
