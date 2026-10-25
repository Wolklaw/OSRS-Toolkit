from __future__ import annotations

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def _track(settings: Settings, *names: str) -> list[int]:
    repository = JournalRepository(settings.journal_path("111"))
    return [repository.track(1, name, 10, target_buy=100, target_sell=200) for name in names]


def test_many_positions_become_supplies_in_one_call(signed_in: TestClient, settings: Settings):
    ids = _track(settings, "Shark", "Karambwan", "Prayer potion(4)")

    response = signed_in.post(
        "/api/positions/bulk", json={"action": "supplies", "position_ids": ids}
    )

    assert response.json() == {"ok": True, "updated": 3}
    statuses = {row["status"] for row in signed_in.get("/api/positions").json()["positions"]}
    assert statuses == {"Supplies"}


def test_many_positions_can_be_removed_in_one_call(signed_in: TestClient, settings: Settings):
    ids = _track(settings, "Shark", "Karambwan")

    response = signed_in.post("/api/positions/bulk", json={"action": "remove", "position_ids": ids})

    assert response.json() == {"ok": True, "updated": 2}
    assert signed_in.get("/api/positions").json() == {"positions": []}


def test_ids_that_are_not_yours_are_ignored(signed_in: TestClient, settings: Settings):
    [mine] = _track(settings, "Shark")

    response = signed_in.post(
        "/api/positions/bulk", json={"action": "remove", "position_ids": [mine, 9_999]}
    )

    assert response.json()["updated"] == 1


def test_a_bad_bulk_request_is_refused(signed_in: TestClient):
    assert (
        signed_in.post(
            "/api/positions/bulk", json={"action": "explode", "position_ids": [1]}
        ).status_code
        == 422
    )
    assert (
        signed_in.post(
            "/api/positions/bulk", json={"action": "remove", "position_ids": []}
        ).status_code
        == 422
    )


def test_bulk_changes_need_a_session(client: TestClient):
    response = client.post("/api/positions/bulk", json={"action": "remove", "position_ids": [1]})

    assert response.status_code == 401


def test_the_dashboard_offers_the_selection_controls(signed_in: TestClient, settings: Settings):
    _track(settings, "Shark")

    page = signed_in.get("/").text

    assert 'class="bulk-pick"' in page
    assert "bulkPositions('supplies')" in page
