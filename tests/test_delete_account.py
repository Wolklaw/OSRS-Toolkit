"""Deleting an account: the journal file, the accounts-db row, and the session all have to
go together, or the disclaimer on /about promising this is possible would be lying."""

from __future__ import annotations

from fastapi.testclient import TestClient

from toolkit_web.config import Settings


def test_deleting_removes_the_journal_file(signed_in: TestClient, settings: Settings) -> None:
    signed_in.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": 1, "buy_price": 100, "sell_price": 200},
    )
    journal_path = settings.journal_path("111")
    assert journal_path.exists()

    signed_in.post("/api/account/delete")

    assert not journal_path.exists()


def test_deleting_removes_the_account(signed_in: TestClient) -> None:
    signed_in.post("/api/account/delete")

    # The session cookie is cleared, so the same client is now signed out.
    assert signed_in.get("/api/me").status_code == 401


def test_deleting_needs_a_session(client: TestClient) -> None:
    assert client.post("/api/account/delete").status_code == 401


def test_a_desktop_token_cannot_delete_the_account(
    signed_in: TestClient, settings: Settings
) -> None:
    """The one thing re-pairing could not undo, so it is browser-only -- the same line drawn
    around minting and revoking desktop credentials themselves."""
    token = signed_in.post("/api/desktop-tokens", json={"label": "Desktop"}).json()["token"]
    signed_in.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": 1, "buy_price": 100, "sell_price": 200},
    )

    bare = TestClient(signed_in.app, base_url="https://testserver")
    response = bare.post("/api/account/delete", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert settings.journal_path("111").exists()


def test_deleting_cascades_characters_and_desktop_tokens(signed_in: TestClient) -> None:
    signed_in.post("/api/characters", json={"name": "Wolklaw"})
    signed_in.post("/api/desktop-tokens", json={"label": "This PC"})

    signed_in.post("/api/account/delete")

    # Signing back in as the same discord id is a fresh account: nothing survived.
    signed_in.post("/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"})
    assert signed_in.get("/api/characters").json()["characters"] == []
    assert signed_in.get("/api/desktop-tokens").json()["tokens"] == []


def test_deleting_does_not_touch_another_account(client: TestClient, settings: Settings) -> None:
    client.post("/auth/dev-login", json={"discord_user_id": "111", "username": "First"})
    client.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": 1, "buy_price": 100, "sell_price": 200},
    )

    client.post("/auth/dev-login", json={"discord_user_id": "222", "username": "Second"})
    client.post("/api/account/delete")

    assert settings.journal_path("111").exists()
