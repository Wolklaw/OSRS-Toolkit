from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def test_about_is_readable_without_signing_in(client: TestClient):
    response = client.get("/about")

    assert response.status_code == 200
    assert "not affiliated with" in response.text.lower()


def test_help_is_readable_without_signing_in(client: TestClient):
    response = client.get("/help")

    assert response.status_code == 200
    assert "Connecting the RuneLite plugin" in response.text


def test_a_signed_out_visitor_is_offered_a_way_back_to_signing_in(client: TestClient):
    response = client.get("/about")

    assert ">Sign in</a>" in response.text
    assert 'class="sidebar"' not in response.text


def test_about_still_renders_for_a_signed_in_account(signed_in: TestClient):
    response = signed_in.get("/about")

    assert response.status_code == 200
    assert 'class="sidebar"' in response.text


def test_the_profile_page_needs_a_session(client: TestClient):
    assert client.get("/profile").status_code == 401


def test_the_profile_page_counts_what_it_is_holding(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000)
    repository.add("Dragon bones", quantity=10, buy_price=2_000, sell_price=2_200)

    response = signed_in.get("/profile")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Wolklaw" in response.text
    assert "Your data" in response.text


def test_the_profile_page_says_whether_the_plugin_is_connected(signed_in: TestClient):
    assert "Not connected" in signed_in.get("/profile").text
    assert "Replace token" not in signed_in.get("/profile").text

    with patch("toolkit_web.routes.account.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    page = signed_in.get("/profile").text
    assert "Replace token" in page
    assert "Disconnect" in page


def test_named_characters_are_managed_from_the_profile(signed_in: TestClient):
    signed_in.post("/api/characters", json={"name": "Alt Account"})

    response = signed_in.get("/profile")

    assert "Alt Account" in response.text


def test_desktop_tokens_are_shown_and_reachable_from_the_profile(signed_in: TestClient):
    minted = signed_in.post("/api/desktop-tokens", json={"label": "This PC"})
    assert minted.status_code == 201

    response = signed_in.get("/profile")

    assert "This PC" in response.text
    assert minted.json()["token"] not in response.text


def test_a_revoked_desktop_token_drops_off_the_profile(signed_in: TestClient):
    signed_in.post("/api/desktop-tokens", json={"label": "This PC"})
    token_id = signed_in.get("/api/desktop-tokens").json()["tokens"][0]["id"]

    signed_in.delete(f"/api/desktop-tokens/{token_id}")

    assert "This PC" not in signed_in.get("/profile").text


def test_a_discord_account_cannot_rename_itself_here(signed_in: TestClient):
    response = signed_in.patch("/api/me", json={"username": "Someone Else"})

    assert response.status_code == 409
    assert signed_in.get("/api/me").json()["username"] == "Wolklaw"


def test_a_recovery_account_can_rename_itself(client: TestClient):
    client.post("/auth/recovery/create", json={"display_name": "Player"})

    response = client.patch("/api/me", json={"username": "Lord Wolklaw"})

    assert response.status_code == 200
    assert client.get("/api/me").json()["username"] == "Lord Wolklaw"


def test_a_blank_display_name_is_refused(client: TestClient):
    client.post("/auth/recovery/create", json={"display_name": "Player"})

    response = client.patch("/api/me", json={"username": "   "})

    assert response.status_code == 422
    assert client.get("/api/me").json()["username"] == "Player"


def test_a_recovery_account_is_offered_the_rename_and_a_discord_one_is_not(
    client: TestClient,
):
    client.post("/auth/recovery/create", json={"display_name": "Player"})
    assert "Display name" in client.get("/profile").text

    client.post("/auth/dev-login", json={"discord_user_id": "222", "username": "Wolklaw"})
    assert "Display name" not in client.get("/profile").text
