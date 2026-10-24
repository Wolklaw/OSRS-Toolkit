from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutItem, LoadoutSnapshot

from toolkit_web.config import Settings


def test_the_dashboard_shows_nothing_with_no_snapshot_and_no_positions(signed_in: TestClient):
    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Net worth composition" not in response.text


def test_the_dashboard_shows_composition_from_a_snapshot(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="char-1",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(
                LoadoutItem(
                    item_id=4151, item_name="Abyssal whip", quantity=1, unit_value=1_500_000
                ),
            ),
            inventory=(
                LoadoutItem(item_id=995, item_name="Coins", quantity=2_000_000, unit_value=1),
            ),
            bank=(
                LoadoutItem(
                    item_id=11802, item_name="Armadyl godsword", quantity=1, unit_value=40_000_000
                ),
            ),
            skills={},
        )
    )

    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Net worth composition" in response.text
    assert "Cash: 2,000,000 gp" in response.text
    assert "Carried gear: 1,500,000 gp" in response.text
    assert "Bank: 40,000,000 gp" in response.text
    assert "GE in-flight" not in response.text


def test_ge_in_flight_shows_up_even_without_a_snapshot(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 1, 1_000_000, 1_100_000)

    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Net worth composition" in response.text
    assert "GE in-flight: 1,000,000 gp" in response.text
