"""Opening a position from a suggestion, and recording what actually happened to it.

Between them these are the whole point of a journal, and until now the web front end could
only read positions the plugin had opened for it — never open one, never close one out.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings

_WHIP = {
    "item_id": 4151,
    "item_name": "Abyssal whip",
    "quantity": 5,
    "target_buy": 1_450_000,
    "target_sell": 1_560_000,
}


def test_tracking_a_flip_opens_a_pending_position(signed_in: TestClient):
    response = signed_in.post("/api/positions", json=_WHIP)

    assert response.status_code == 201
    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["item_name"] == "Abyssal whip"
    assert position["status"] == "Pending buy"
    assert position["target_sell"] == 1_560_000


def test_a_tracked_flip_shows_up_on_the_dashboard(signed_in: TestClient):
    signed_in.post("/api/positions", json=_WHIP)

    response = signed_in.get("/")

    assert "Abyssal whip" in response.text
    assert "Nothing tracked yet" not in response.text


def test_a_position_is_tagged_with_the_character_being_looked_at(signed_in: TestClient):
    """Otherwise a plan opened while viewing one account would later read as belonging to
    every one of them, which is exactly what the character switcher exists to prevent."""
    signed_in.cookies.set("active_character", "hash-a")

    signed_in.post("/api/positions", json=_WHIP)

    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["account_hash"] == "hash-a"


def test_an_unknown_strategy_falls_back_rather_than_being_written_through(
    signed_in: TestClient,
):
    signed_in.post("/api/positions", json={**_WHIP, "strategy": "Reckless (instant)"})

    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["strategy"] in ("Balanced (1–4h)",)


def test_tracking_needs_a_session(client: TestClient):
    assert client.post("/api/positions", json=_WHIP).status_code == 401


def test_a_position_missing_its_prices_is_refused(signed_in: TestClient):
    response = signed_in.post("/api/positions", json={"item_name": "Whip", "quantity": 1})

    assert response.status_code == 422
    assert signed_in.get("/api/positions").json()["positions"] == []


# -- updating ------------------------------------------------------------------------------


def test_recording_fills_completes_a_position_and_realizes_its_profit(signed_in: TestClient):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]

    response = signed_in.patch(
        f"/api/positions/{position_id}",
        json={
            "status": "Completed",
            "buy_fills": [[5, 1_448_000]],
            "sale_fills": [[5, 1_555_000]],
        },
    )

    assert response.status_code == 200
    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["status"] == "Completed"
    # (1,555,000 - 1,448,000 - 2% tax of 31,100) * 5
    assert position["realized_profit"] == 379_500


def test_a_completed_position_reaches_the_performance_page(signed_in: TestClient):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]
    signed_in.patch(
        f"/api/positions/{position_id}",
        json={
            "status": "Completed",
            "buy_fills": [[5, 1_448_000]],
            "sale_fills": [[5, 1_555_000]],
        },
    )

    response = signed_in.get("/performance")

    assert "Nothing realized in this period yet" not in response.text


def test_a_partial_buy_can_shrink_the_position_to_what_was_actually_bought(
    signed_in: TestClient,
):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]

    response = signed_in.patch(
        f"/api/positions/{position_id}",
        json={"status": "Bought", "quantity": 3, "buy_fills": [[3, 1_448_000]]},
    )

    assert response.status_code == 200
    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["quantity"] == 3
    assert position["bought_quantity"] == 3


def test_completing_a_position_that_does_not_account_for_everything_is_refused(
    signed_in: TestClient,
):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]

    response = signed_in.patch(
        f"/api/positions/{position_id}",
        json={
            "status": "Completed",
            "buy_fills": [[5, 1_448_000]],
            "sale_fills": [[2, 1_555_000]],
        },
    )

    assert response.status_code == 422
    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["status"] == "Pending buy"


def test_a_status_the_journal_does_not_recognise_is_refused(signed_in: TestClient):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]

    response = signed_in.patch(f"/api/positions/{position_id}", json={"status": "Vibing"})

    assert response.status_code == 422


def test_an_update_that_sends_no_fills_leaves_the_recorded_ones_alone(
    signed_in: TestClient, settings: Settings
):
    """A synced position's partial fills are history nobody typed. Changing its status from
    the web should never be a way to lose them."""
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        4151, "Abyssal whip", 5, target_buy=1_450_000, target_sell=1_560_000
    )
    repository.update_tracked(position_id, "Bought", None, None, buy_fills=[[3, 1_448_000]])

    signed_in.patch(f"/api/positions/{position_id}", json={"status": "Listed for sale"})

    [position] = signed_in.get("/api/positions").json()["positions"]
    assert position["status"] == "Listed for sale"
    assert position["bought_quantity"] == 3


def test_a_malformed_fill_is_refused_before_it_reaches_the_journal(signed_in: TestClient):
    position_id = signed_in.post("/api/positions", json=_WHIP).json()["position_id"]

    response = signed_in.patch(
        f"/api/positions/{position_id}",
        json={"status": "Bought", "buy_fills": [[5]]},
    )

    assert response.status_code == 422


def test_updating_needs_a_session(client: TestClient):
    assert client.patch("/api/positions/1", json={"status": "Bought"}).status_code == 401
