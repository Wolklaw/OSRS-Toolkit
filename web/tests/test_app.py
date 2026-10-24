from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
)

from toolkit_web.config import Settings


def test_reading_your_own_journal_needs_a_session(client: TestClient):
    assert client.get("/api/positions").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_dev_login_signs_a_session_in(signed_in: TestClient):
    response = signed_in.get("/api/me")

    assert response.status_code == 200
    assert response.json() == {
        "discord_user_id": "111",
        "username": "Wolklaw",
        "avatar_url": None,
        "paired": False,
    }


def test_dev_login_is_absent_when_not_enabled(settings: Settings):
    from toolkit_web.app import create_app

    disabled = Settings(**{**settings.__dict__, "dev_login_enabled": False})
    with TestClient(create_app(disabled)) as no_dev_login:
        response = no_dev_login.post(
            "/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"}
        )

    assert response.status_code == 404


def test_logout_clears_the_session(signed_in: TestClient):
    signed_in.post("/auth/logout")

    assert signed_in.get("/api/me").status_code == 401


def test_discord_login_without_a_configured_application_fails_clearly(settings: Settings):
    from toolkit_web.app import create_app

    unconfigured = Settings(**{**settings.__dict__, "discord_client_id": None})
    with TestClient(create_app(unconfigured)) as unconfigured_client:
        response = unconfigured_client.get("/auth/discord/login", follow_redirects=False)

    assert response.status_code == 503


def test_discord_login_redirects_to_discord_with_a_state_cookie(client: TestClient):
    response = client.get("/auth/discord/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://discord.com/api/oauth2/authorize?")
    assert "oauth_state" in response.cookies


def test_a_callback_whose_state_does_not_match_the_cookie_is_rejected(client: TestClient):
    client.get("/auth/discord/login", follow_redirects=False)

    response = client.get(
        "/auth/discord/callback",
        params={"code": "abc", "state": "not-the-cookie-value"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_a_callback_discord_never_confirmed_is_rejected(client: TestClient):
    login = client.get("/auth/discord/login", follow_redirects=False)
    state = login.cookies["oauth_state"]

    with (
        patch("toolkit_web.app.discord_oauth.exchange_code", return_value="access-token"),
        patch("toolkit_web.app.discord_oauth.fetch_user", return_value=None),
    ):
        response = client.get(
            "/auth/discord/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 502


def test_a_successful_callback_signs_the_user_in(client: TestClient):
    from toolkit_web.discord_oauth import DiscordUser

    login = client.get("/auth/discord/login", follow_redirects=False)
    state = login.cookies["oauth_state"]
    user = DiscordUser(id="111", username="Wolklaw", avatar_url=None)

    with (
        patch("toolkit_web.app.discord_oauth.exchange_code", return_value="access-token"),
        patch("toolkit_web.app.discord_oauth.fetch_user", return_value=user),
    ):
        response = client.get(
            "/auth/discord/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "toolkit_session" in response.cookies
    assert client.get("/api/me").json()["username"] == "Wolklaw"

    replay = client.get(
        "/auth/discord/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )
    assert replay.status_code == 400


def test_setting_a_pairing_token_that_the_service_accepts(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=True):
        response = signed_in.put("/api/pairing-token", json={"token": "good-token"})

    assert response.json() == {"ok": True, "verified": True}
    assert signed_in.get("/api/me").json()["paired"] is True


def test_setting_a_pairing_token_the_service_rejects(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=False):
        response = signed_in.put("/api/pairing-token", json={"token": "bad-token"})

    assert response.status_code == 422
    assert signed_in.get("/api/me").json()["paired"] is False


def test_a_token_saved_while_the_service_cannot_be_reached_is_still_kept(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=None):
        response = signed_in.put("/api/pairing-token", json={"token": "some-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "verified": False}
    assert signed_in.get("/api/me").json()["paired"] is True


def test_clearing_the_pairing_token(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    signed_in.delete("/api/pairing-token")

    assert signed_in.get("/api/me").json()["paired"] is False


def test_pairing_token_needs_a_session(client: TestClient):
    assert client.put("/api/pairing-token", json={"token": "x"}).status_code == 401


def test_a_fresh_account_has_an_empty_journal(signed_in: TestClient):
    assert signed_in.get("/api/positions").json() == {"positions": []}
    assert signed_in.get("/api/trades").json() == {"trades": []}
    assert signed_in.get("/api/net-worth").json() == {"points": []}
    assert signed_in.get("/api/loadout").json() == {"loadout": None}


def test_a_tracked_position_appears_with_its_computed_fields(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000)

    response = signed_in.get("/api/positions")

    [position] = response.json()["positions"]
    assert position["item_name"] == "Abyssal whip"
    assert position["status"] == "Pending buy"
    assert "estimated_profit" in position


def test_positions_can_be_filtered_to_one_synced_character(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(1, "Item A", 1, target_buy=100, target_sell=200, account_hash="hash-a")
    repository.track(2, "Item B", 1, target_buy=100, target_sell=200, account_hash="hash-b")
    repository.track(3, "Item C", 1, target_buy=100, target_sell=200)

    signed_in.cookies.set("active_character", "hash-a")
    response = signed_in.get("/api/positions")

    names = {position["item_name"] for position in response.json()["positions"]}
    assert names == {"Item A", "Item C"}


def test_two_different_accounts_see_two_different_journals(client: TestClient):
    client.post("/auth/dev-login", json={"discord_user_id": "aaa", "username": "First"})
    settings = client.app.state.settings
    JournalRepository(settings.journal_path("aaa")).track(
        1, "Item A", 1, target_buy=100, target_sell=200
    )

    client.post("/auth/dev-login", json={"discord_user_id": "bbb", "username": "Second"})

    assert client.get("/api/positions").json() == {"positions": []}


def test_a_manual_trade_appears_in_trades(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add("Dragon bones", quantity=100, buy_price=2_000, sell_price=2_200)

    [trade] = signed_in.get("/api/trades").json()["trades"]

    assert trade["item_name"] == "Dragon bones"
    assert trade["quantity"] == 100
    assert "profit" in trade


def test_the_latest_loadout_snapshot_is_returned_with_item_totals(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(
                LoadoutItem(
                    item_id=4151, item_name="Abyssal whip", quantity=1, unit_value=2_000_000
                ),
            ),
            inventory=(),
            bank=(),
            skills={"Attack": 99},
        )
    )

    loadout = signed_in.get("/api/loadout").json()["loadout"]

    assert loadout["account_name"] == "Wolklaw"
    [item] = loadout["equipment"]
    assert item["total_value"] == 2_000_000
    assert loadout["skills"] == {"Attack": 99}


def test_journal_reads_need_a_session(client: TestClient):
    for path in (
        "/api/positions",
        "/api/trades",
        "/api/net-worth",
        "/api/loadout",
        "/api/activity",
    ):
        assert client.get(path).status_code == 401


def test_characters_is_empty_without_a_pairing_token(signed_in: TestClient):
    assert signed_in.get("/api/accounts").json() == {"accounts": []}


def test_characters_delegates_to_the_sync_service_once_paired(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    known = [{"account_hash": "abc", "account_name": "Wolklaw", "last_seen": "..."}]
    with patch("toolkit_web.importing.HttpSyncSource.known_accounts", return_value=known):
        response = signed_in.get("/api/accounts")

    assert response.json() == {"accounts": known}


def test_a_live_ge_offer_shows_on_the_dashboard(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    slot_payload = {
        "0": {
            "itemId": 4151,
            "itemName": "Abyssal whip",
            "offerPrice": 2_000_000,
            "totalQuantity": 1,
            "quantityFilled": 0,
            "spentGp": 0,
            "state": "BUYING",
        }
    }
    with (
        patch(
            "toolkit_web.importing.HttpSyncSource.offer_state_payload",
            return_value=slot_payload,
        ),
        patch("toolkit_web.importing.HttpSyncSource.offer_screen_payload", return_value=None),
    ):
        signed_in.cookies.set("active_character", "hash-a")
        response = signed_in.get("/")

    assert "Abyssal whip" in response.text
    assert "Buying" in response.text


def test_a_signed_in_account_with_a_real_avatar_renders_it(client: TestClient):
    client.post(
        "/auth/dev-login",
        json={
            "discord_user_id": "111",
            "username": "Wolklaw",
            "avatar": "https://cdn.discordapp.com/avatars/111/abc.png",
        },
    )

    response = client.get("/")

    assert 'src="https://cdn.discordapp.com/avatars/111/abc.png"' in response.text
