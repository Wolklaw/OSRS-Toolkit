from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository

from toolkit_web.config import Settings


def test_the_landing_page_shows_a_sign_in_link_when_signed_out(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" in response.text
    assert "/auth/discord/login" in response.text


def test_the_root_page_shows_the_dashboard_once_signed_in(signed_in: TestClient):
    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text
    assert "Wolklaw" in response.text


def test_an_unpaired_account_sees_the_connect_panel(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Connect your RuneLite plugin" in response.text


def test_a_paired_account_does_not_see_the_connect_panel(signed_in: TestClient):
    from unittest.mock import patch

    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    response = signed_in.get("/")

    assert "Connect your RuneLite plugin" not in response.text


def test_an_empty_journal_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Nothing tracked yet" in response.text
    # No stray Jinja template errors leaking into the page from a None/empty computation.
    assert "Undefined" not in response.text


def test_a_tracked_position_appears_on_the_dashboard_with_its_numbers(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 2, target_buy=1_000_000, target_sell=1_200_000)

    response = signed_in.get("/")

    assert "Abyssal whip" in response.text
    # 2 * 1,000,000 target buy, no fills recorded yet — the planned outlay is what should show.
    assert "2,000,000" in response.text


def test_net_worth_shows_the_most_recent_reading_not_an_older_one(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    for value in (1_000_000, 5_000_000):
        repository.save_loadout_snapshot(_bare_snapshot(total_value=value))

    response = signed_in.get("/")

    assert "5,000,000" in response.text
    assert "1,000,000" not in response.text


def _bare_snapshot(total_value: int):
    from osrs_toolkit.journal import LoadoutItem, LoadoutSnapshot

    return LoadoutSnapshot(
        account_hash="abc123",
        account_name="Wolklaw",
        captured_at=datetime.now(UTC).isoformat(),
        equipment=(
            LoadoutItem(item_id=995, item_name="Coins", quantity=total_value, unit_value=1),
        ),
        inventory=(),
        bank=(),
        skills={},
    )
