from __future__ import annotations

from fastapi.testclient import TestClient


def test_adding_a_character_returns_it(signed_in: TestClient):
    response = signed_in.post("/api/characters", json={"name": "Wolklaw"})

    assert response.status_code == 201
    character = response.json()["character"]
    assert character["name"] == "Wolklaw"
    assert isinstance(character["character_id"], int)


def test_an_added_character_appears_in_the_list(signed_in: TestClient):
    signed_in.post("/api/characters", json={"name": "Wolklaw"})

    [character] = signed_in.get("/api/characters").json()["characters"]
    assert character["name"] == "Wolklaw"


def test_a_blank_name_is_refused(signed_in: TestClient):
    assert signed_in.post("/api/characters", json={"name": "  "}).status_code == 422


def test_a_duplicate_name_for_the_same_account_is_refused(signed_in: TestClient):
    signed_in.post("/api/characters", json={"name": "Wolklaw"})

    response = signed_in.post("/api/characters", json={"name": "Wolklaw"})

    assert response.status_code == 409
    assert len(signed_in.get("/api/characters").json()["characters"]) == 1


def test_two_different_accounts_can_use_the_same_character_name(client: TestClient):
    client.post("/auth/dev-login", json={"discord_user_id": "aaa", "username": "First"})
    client.post("/api/characters", json={"name": "Ironman"})

    client.post("/auth/dev-login", json={"discord_user_id": "bbb", "username": "Second"})
    response = client.post("/api/characters", json={"name": "Ironman"})

    assert response.status_code == 201


def test_deleting_a_character_removes_it_from_the_list(signed_in: TestClient):
    created = signed_in.post("/api/characters", json={"name": "Wolklaw"}).json()["character"]

    signed_in.delete(f"/api/characters/{created['character_id']}")

    assert signed_in.get("/api/characters").json()["characters"] == []


def test_deleting_a_character_that_never_existed_is_harmless(signed_in: TestClient):
    assert signed_in.delete("/api/characters/999999").status_code == 200


def test_characters_need_a_session(client: TestClient):
    assert client.get("/api/characters").status_code == 401
    assert client.post("/api/characters", json={"name": "x"}).status_code == 401


def test_a_trade_can_be_attributed_to_a_character(signed_in: TestClient):
    character = signed_in.post("/api/characters", json={"name": "Wolklaw"}).json()["character"]

    signed_in.post(
        "/api/trades",
        json={
            "item_name": "Dragon bones",
            "quantity": 100,
            "buy_price": 2_000,
            "sell_price": 2_200,
            "character_id": character["character_id"],
        },
    )

    response = signed_in.get("/trades")
    assert "Wolklaw" in response.text
    assert "Dragon bones" in response.text


def test_a_trade_with_no_character_shows_unattributed(signed_in: TestClient):
    signed_in.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": 1, "buy_price": 100, "sell_price": 200},
    )

    response = signed_in.get("/trades")

    assert "Whip" in response.text


def test_deleting_a_character_leaves_its_trades_in_the_log_unattributed(signed_in: TestClient):
    character = signed_in.post("/api/characters", json={"name": "Wolklaw"}).json()["character"]
    signed_in.post(
        "/api/trades",
        json={
            "item_name": "Dragon bones",
            "quantity": 100,
            "buy_price": 2_000,
            "sell_price": 2_200,
            "character_id": character["character_id"],
        },
    )

    signed_in.delete(f"/api/characters/{character['character_id']}")

    response = signed_in.get("/trades")
    assert "Dragon bones" in response.text
    assert "<td>Wolklaw</td>" not in response.text
    assert "<td>—</td>" in response.text


def test_a_character_from_one_account_cannot_be_attributed_by_another(client: TestClient):
    client.post("/auth/dev-login", json={"discord_user_id": "aaa", "username": "First"})
    character = client.post("/api/characters", json={"name": "Alt"}).json()["character"]

    client.post("/auth/dev-login", json={"discord_user_id": "bbb", "username": "Second"})
    client.post(
        "/api/trades",
        json={
            "item_name": "Whip",
            "quantity": 1,
            "buy_price": 100,
            "sell_price": 200,
            "character_id": character["character_id"],
        },
    )

    response = client.get("/trades")
    assert "Alt" not in response.text
