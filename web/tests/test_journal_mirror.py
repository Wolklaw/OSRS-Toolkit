from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def _desktop(signed_in: TestClient) -> tuple[TestClient, dict[str, str]]:
    token = signed_in.post("/api/desktop-tokens", json={"label": "Desktop"}).json()["token"]
    bare = TestClient(signed_in.app, base_url="https://testserver")
    return bare, {"Authorization": f"Bearer {token}"}


def _add_trade(client: TestClient, name: str, **headers: str) -> dict:
    response = client.post(
        "/api/trades",
        json={"item_name": name, "quantity": 1, "buy_price": 10, "sell_price": 20},
        headers=headers or None,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_version_starts_empty_and_moves_with_the_journal(signed_in: TestClient) -> None:
    first = signed_in.get("/api/journal/version").json()
    assert first["counts"] == {"trades": 0, "tracked_trades": 0}

    _add_trade(signed_in, "Abyssal whip")

    second = signed_in.get("/api/journal/version").json()
    assert second["counts"]["trades"] == 1
    assert second["version"] > first["version"]


def test_version_is_steady_when_nothing_happens(signed_in: TestClient) -> None:
    _add_trade(signed_in, "Abyssal whip")
    assert signed_in.get("/api/journal/version").json() == (
        signed_in.get("/api/journal/version").json()
    )


def test_version_still_answers_when_the_sync_service_is_unreachable(
    signed_in: TestClient,
) -> None:
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "a-pairing-token"})

    response = signed_in.get("/api/journal/version")

    assert response.status_code == 200
    assert "counts" in response.json()


def test_the_desktops_own_poll_drains_the_plugins_queue(signed_in: TestClient) -> None:
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "a-pairing-token"})

    with patch("toolkit_web.importing.RuneLiteSyncImporter") as importer:
        signed_in.get("/api/journal/version")

    importer.return_value.import_pending.assert_called_once()


def test_the_desktop_can_pull_what_the_website_holds(signed_in: TestClient) -> None:
    _add_trade(signed_in, "Abyssal whip")
    bare, headers = _desktop(signed_in)

    payload = bare.get("/api/journal/pull", headers=headers).json()

    assert [row["item_name"] for row in payload["trades"]] == ["Abyssal whip"]
    assert payload["tracked_trades"] == []


def test_pulling_since_a_watermark_carries_only_what_changed(signed_in: TestClient) -> None:
    _add_trade(signed_in, "Abyssal whip")
    watermark = signed_in.get("/api/journal/version").json()["version"]
    _add_trade(signed_in, "Dragon bones")

    bare, headers = _desktop(signed_in)
    payload = bare.get("/api/journal/pull", params={"since": watermark}, headers=headers).json()

    assert [row["item_name"] for row in payload["trades"]] == ["Dragon bones"]


def test_the_desktop_can_seed_an_empty_website(signed_in: TestClient) -> None:
    bare, headers = _desktop(signed_in)
    seed = {
        "trades": [
            {
                "sync_uid": "aaa",
                "recorded_at": "2026-08-11T06:00:00+00:00",
                "updated_at": "2026-08-11T06:00:00+00:00",
                "item_name": "Abyssal whip",
                "quantity": 1,
                "buy_price": 1_500_000,
                "sell_price": 1_650_000,
            }
        ],
        "tracked_trades": [],
    }

    applied = bare.post("/api/journal/push", json=seed, headers=headers).json()

    assert applied["inserted"] == 1
    assert [t["item_name"] for t in signed_in.get("/api/trades").json()["trades"]] == [
        "Abyssal whip"
    ]


def test_pushing_the_same_rows_twice_does_not_duplicate(signed_in: TestClient) -> None:
    bare, headers = _desktop(signed_in)
    seed = {
        "trades": [
            {
                "sync_uid": "aaa",
                "recorded_at": "2026-08-11T06:00:00+00:00",
                "updated_at": "2026-08-11T06:00:00+00:00",
                "item_name": "Abyssal whip",
                "quantity": 1,
                "buy_price": 1,
                "sell_price": 2,
            }
        ]
    }
    bare.post("/api/journal/push", json=seed, headers=headers)
    second = bare.post("/api/journal/push", json=seed, headers=headers).json()

    assert second["inserted"] == 0
    assert len(signed_in.get("/api/trades").json()["trades"]) == 1


def test_a_round_trip_leaves_both_sides_agreeing(signed_in: TestClient) -> None:
    _add_trade(signed_in, "Abyssal whip")
    bare, headers = _desktop(signed_in)

    pulled = bare.get("/api/journal/pull", headers=headers).json()
    applied = bare.post("/api/journal/push", json=pulled, headers=headers).json()

    assert applied["inserted"] == 0, "its own rows came back as news"
    assert len(signed_in.get("/api/trades").json()["trades"]) == 1


def test_the_mirror_needs_an_account(client: TestClient) -> None:
    assert client.get("/api/journal/version").status_code == 401
    assert client.get("/api/journal/pull").status_code == 401
    assert client.post("/api/journal/push", json={}).status_code == 401


def test_one_account_cannot_pull_anothers_journal(
    client: TestClient, signed_in: TestClient
) -> None:
    _add_trade(signed_in, "Abyssal whip")
    bare, headers = _desktop(signed_in)

    client.post("/auth/dev-login", json={"discord_user_id": "222", "username": "Someone"})
    _add_trade(client, "Dragon bones")

    mine = bare.get("/api/journal/pull", headers=headers).json()
    assert [row["item_name"] for row in mine["trades"]] == ["Abyssal whip"]


def test_a_trade_keeps_its_character_through_the_new_key(signed_in: TestClient) -> None:
    created_character = signed_in.post("/api/characters", json={"name": "Wolklaw"}).json()
    character = created_character["character"]
    created = signed_in.post(
        "/api/trades",
        json={
            "item_name": "Abyssal whip",
            "quantity": 1,
            "buy_price": 10,
            "sell_price": 20,
            "character_id": character["character_id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["sync_uid"]

    page = signed_in.get("/trades")
    assert page.status_code == 200
    assert "Wolklaw" in page.text
