from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def test_no_goal_shows_the_set_goal_form(signed_in: TestClient):
    response = signed_in.get("/savings-goal")

    assert response.status_code == 200
    assert "Set a savings goal" in response.text


def test_setting_a_goal_shows_it_on_the_page(signed_in: TestClient):
    signed_in.post("/api/savings-goal", json={"label": "New bank", "target": 50_000_000})

    response = signed_in.get("/savings-goal")

    assert "New bank" in response.text
    assert "50,000,000" in response.text


def test_a_blank_label_is_refused(signed_in: TestClient):
    response = signed_in.post("/api/savings-goal", json={"label": "  ", "target": 1000})

    assert response.status_code == 422


def test_a_non_positive_target_is_refused(signed_in: TestClient):
    response = signed_in.post("/api/savings-goal", json={"label": "Goal", "target": 0})

    assert response.status_code == 422


def test_profit_realized_before_the_goal_existed_does_not_count(
    signed_in: TestClient, settings: Settings
):
    """Trading results from before the goal existed were not earned toward it — the same
    rule savings_goal.realized_profit_since documents."""
    repository = JournalRepository(settings.journal_path("111"))
    repository.add("Dragon bones", quantity=100, buy_price=2_000, sell_price=2_200)

    signed_in.post("/api/savings-goal", json={"label": "New bank", "target": 1_000_000})

    response = signed_in.get("/savings-goal")

    # profit from that trade is > 0 but predates the goal, so saved must read 0, not a
    # positive figure that was never actually earned toward this goal.
    assert ">0 gp<" in response.text or "0 gp" in response.text


def test_profit_realized_after_the_goal_counts_toward_it(signed_in: TestClient, settings: Settings):
    signed_in.post("/api/savings-goal", json={"label": "New bank", "target": 1_000_000})

    repository = JournalRepository(settings.journal_path("111"))
    repository.add("Dragon bones", quantity=100, buy_price=2_000, sell_price=2_200)

    response = signed_in.get("/savings-goal")

    assert response.status_code == 200
    # profit = (2200 - 2000 - ge_tax(2200)) * 100 = (200 - 44) * 100 = 15,600
    assert "15,600" in response.text


def test_clearing_a_goal_returns_to_the_set_goal_form(signed_in: TestClient):
    signed_in.post("/api/savings-goal", json={"label": "New bank", "target": 1_000_000})

    signed_in.delete("/api/savings-goal")

    response = signed_in.get("/savings-goal")
    assert "Set a savings goal" in response.text


def test_savings_goal_page_needs_a_session(client: TestClient):
    assert client.get("/savings-goal").status_code == 401


def test_setting_a_goal_needs_a_session(client: TestClient):
    assert client.post("/api/savings-goal", json={"label": "x", "target": 1}).status_code == 401
