from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade

from toolkit_web.config import Settings

# -- manual trades, via the API ------------------------------------------------------------


def test_adding_a_trade_returns_its_id(signed_in: TestClient):
    response = signed_in.post(
        "/api/trades",
        json={
            "item_name": "Dragon bones",
            "quantity": 100,
            "buy_price": 2_000,
            "sell_price": 2_200,
        },
    )

    assert response.status_code == 201
    assert isinstance(response.json()["trade_id"], int)


def test_an_added_trade_shows_up_in_the_log(signed_in: TestClient):
    signed_in.post(
        "/api/trades",
        json={
            "item_name": "Dragon bones",
            "quantity": 100,
            "buy_price": 2_000,
            "sell_price": 2_200,
        },
    )

    [trade] = signed_in.get("/api/trades").json()["trades"]
    assert trade["item_name"] == "Dragon bones"


def test_a_trade_missing_a_field_is_refused(signed_in: TestClient):
    response = signed_in.post("/api/trades", json={"item_name": "Whip", "quantity": 1})

    assert response.status_code == 422
    assert signed_in.get("/api/trades").json()["trades"] == []


def test_a_negative_quantity_is_refused(signed_in: TestClient):
    response = signed_in.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": -1, "buy_price": 100, "sell_price": 200},
    )

    assert response.status_code == 422


def test_a_blank_item_name_is_refused(signed_in: TestClient):
    response = signed_in.post(
        "/api/trades",
        json={"item_name": "   ", "quantity": 1, "buy_price": 100, "sell_price": 200},
    )

    assert response.status_code == 422


def test_deleting_a_trade_removes_it(signed_in: TestClient):
    added = signed_in.post(
        "/api/trades",
        json={"item_name": "Whip", "quantity": 1, "buy_price": 100, "sell_price": 200},
    ).json()

    signed_in.delete(f"/api/trades/{added['trade_id']}")

    assert signed_in.get("/api/trades").json()["trades"] == []


def test_deleting_a_trade_that_never_existed_is_harmless(signed_in: TestClient):
    assert signed_in.delete("/api/trades/999999").status_code == 200


def test_adding_a_trade_needs_a_session(client: TestClient):
    assert client.post("/api/trades", json={}).status_code == 401


# -- position removal --------------------------------------------------------------------


def test_deleting_a_position_removes_it_from_the_journal(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000
    )

    signed_in.delete(f"/api/positions/{position_id}")

    assert signed_in.get("/api/positions").json()["positions"] == []


def test_deleting_a_position_needs_a_session(client: TestClient):
    assert client.delete("/api/positions/1").status_code == 401


# -- trades page --------------------------------------------------------------------------


def test_the_trades_page_shows_an_added_trade(signed_in: TestClient):
    signed_in.post(
        "/api/trades",
        json={
            "item_name": "Dragon bones",
            "quantity": 100,
            "buy_price": 2_000,
            "sell_price": 2_200,
        },
    )

    response = signed_in.get("/trades")

    assert response.status_code == 200
    assert "Dragon bones" in response.text


def test_the_trades_page_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/trades")

    assert "No manual trades recorded yet" in response.text


def test_the_trades_page_needs_a_session(client: TestClient):
    assert client.get("/trades").status_code == 401


# -- activity page ------------------------------------------------------------------------


def test_the_activity_page_shows_a_ge_fill_with_its_own_label(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_synced_trade(_ge_fill_event())

    response = signed_in.get("/activity")

    assert response.status_code == 200
    assert "Bought Abyssal whip" in response.text
    assert "Grand Exchange" in response.text


def test_the_activity_page_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/activity")

    assert "Nothing recorded yet" in response.text


def test_the_activity_page_needs_a_session(client: TestClient):
    assert client.get("/activity").status_code == 401


def _ge_fill_event() -> SyncedTrade:
    return SyncedTrade(
        event_id="evt-1",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type="ge_fill",
        account_hash="abc123",
        account_name="Wolklaw",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(
                flow="given", item_id=995, item_name="Coins", quantity=1_500_000, unit_value=1
            ),
            SyncedItem(
                flow="received",
                item_id=4151,
                item_name="Abyssal whip",
                quantity=1,
                unit_value=1_500_000,
            ),
        ),
    )
