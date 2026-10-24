from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade

from toolkit_web.config import Settings


def _player_trade(
    event_id: str, counterparty: str, given_value: int, received_value: int, ago: timedelta
) -> SyncedTrade:
    occurred_at = (datetime.now(UTC) - ago).isoformat(timespec="seconds")
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="player_trade",
        account_hash="abc123",
        account_name="Wolklaw",
        counterparty=counterparty,
        direction="exchange",
        metadata={},
        items=(
            SyncedItem(
                flow="given", item_id=995, item_name="Coins", quantity=given_value, unit_value=1
            ),
            SyncedItem(
                flow="received",
                item_id=996,
                item_name="Item",
                quantity=1,
                unit_value=received_value,
            ),
        ),
    )


def test_the_page_shows_the_empty_state_with_no_player_trades(signed_in: TestClient):
    response = signed_in.get("/counterparties")

    assert response.status_code == 200
    assert "No player trades recorded yet" in response.text


def test_trades_with_the_same_person_are_grouped_and_netted(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_synced_trades(
        [
            _player_trade(
                "t1", "Zezima", given_value=1_000, received_value=1_500, ago=timedelta(days=2)
            ),
            _player_trade(
                "t2", "Zezima", given_value=500, received_value=200, ago=timedelta(days=1)
            ),
        ]
    )

    response = signed_in.get("/counterparties")

    assert response.status_code == 200
    assert "Zezima" in response.text
    assert "1,500" in response.text
    assert "1,700" in response.text
    assert "+200" in response.text


def test_different_counterparties_are_kept_separate(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_synced_trades(
        [
            _player_trade(
                "t1", "Alice", given_value=100, received_value=200, ago=timedelta(days=1)
            ),
            _player_trade("t2", "Bob", given_value=300, received_value=100, ago=timedelta(days=1)),
        ]
    )

    response = signed_in.get("/counterparties")

    assert "Alice" in response.text
    assert "Bob" in response.text
    assert "+100" in response.text
    assert "-200" in response.text


def test_the_page_needs_a_session(client: TestClient):
    assert client.get("/counterparties").status_code == 401
