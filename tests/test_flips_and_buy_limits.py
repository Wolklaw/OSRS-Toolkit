from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.models import ItemMapping, MarketPoint

from toolkit_web.config import Settings

# A hand-picked point that clears every one of rank_flips' filters under the Balanced
# strategy: fresh, two-sided liquidity, positive ROI well above the 0.5% floor, stable
# (5m and 1h averages agree exactly, so drift is zero).
_WHIP_ID = 4151


def _market_snapshot():
    now = int(time.time())
    mapping = {
        _WHIP_ID: ItemMapping(
            item_id=_WHIP_ID, name="Abyssal whip", members=True, buy_limit=70, high_alch=100_000
        )
    }
    point = MarketPoint(
        item_id=_WHIP_ID,
        high=1_600_000,
        low=1_500_000,
        high_time=now,
        low_time=now,
        volume_5m=200,
        volume_1h=2_000,
        average_5m=1_550_000,
        average_1h=1_550_000,
        average_high_5m=1_600_000,
        average_low_5m=1_500_000,
        average_high_1h=1_600_000,
        average_low_1h=1_500_000,
        high_volume_5m=100,
        low_volume_5m=100,
        high_volume_1h=1_000,
        low_volume_1h=1_000,
    )
    return mapping, [point]


def _patched_market():
    return patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=_market_snapshot(),
    )


# -- watchlist --------------------------------------------------------------------------------


def test_adding_a_watchlist_item_by_name_and_seeing_it_priced(signed_in: TestClient):
    with _patched_market():
        response = signed_in.post("/api/watchlist", json={"name": "Abyssal whip"})
        assert response.status_code == 201

        page = signed_in.get("/watchlist")

    assert "Abyssal whip" in page.text


def test_adding_an_unknown_item_name_is_rejected(signed_in: TestClient):
    with _patched_market():
        response = signed_in.post("/api/watchlist", json={"name": "Not a real item"})

    assert response.status_code == 404


def test_adding_the_same_item_twice_is_rejected(signed_in: TestClient):
    with _patched_market():
        first = signed_in.post("/api/watchlist", json={"name": "Abyssal whip"})
        second = signed_in.post("/api/watchlist", json={"name": "Abyssal whip"})

    assert first.status_code == 201
    assert second.status_code == 409


def test_removing_a_watchlist_item(signed_in: TestClient):
    with _patched_market():
        signed_in.post("/api/watchlist", json={"name": "Abyssal whip"})
        signed_in.delete(f"/api/watchlist/{_WHIP_ID}")

        page = signed_in.get("/watchlist")

    assert "Abyssal whip" not in page.text
    assert "Nothing on your watchlist yet" in page.text


# -- flips ----------------------------------------------------------------------------------


def test_the_flips_page_shows_a_candidate_that_clears_the_strategy(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips")

    assert response.status_code == 200
    assert "Abyssal whip" in response.text


def test_the_flips_page_degrades_gracefully_when_the_wiki_is_unreachable(signed_in: TestClient):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/flips")

    assert response.status_code == 200
    assert "couldn't be reached" in response.text


def test_the_flips_page_needs_a_session(client: TestClient):
    assert client.get("/flips").status_code == 401


def test_a_cash_stack_too_small_for_anything_shows_the_empty_state(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips", params={"cash_stack": 1000})

    assert "Nothing clears this strategy's bar" in response.text


# -- buy limits -------------------------------------------------------------------------------


def test_buy_limits_shows_an_item_bought_within_the_window(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.add_synced_trade(_ge_fill(quantity=5))

    with _patched_market():
        response = signed_in.get("/buy-limits")

    assert response.status_code == 200
    assert "Abyssal whip" in response.text
    # Bought 5 of a 70 limit -> 65 remaining.
    assert "65" in response.text


def test_buy_limits_shows_the_empty_state_with_nothing_recent(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/buy-limits")

    assert "Nothing bought in the last 4 hours" in response.text


def test_buy_limits_degrades_gracefully_when_the_wiki_is_unreachable(signed_in: TestClient):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/buy-limits")

    assert response.status_code == 200
    assert "couldn't be reached" in response.text


def test_buy_limits_needs_a_session(client: TestClient):
    assert client.get("/buy-limits").status_code == 401


def _ge_fill(quantity: int) -> SyncedTrade:
    return SyncedTrade(
        event_id="evt-buy-1",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type="ge_fill",
        account_hash="abc123",
        account_name="Wolklaw",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem(
                flow="given",
                item_id=995,
                item_name="Coins",
                quantity=quantity * 1_500_000,
                unit_value=1,
            ),
            SyncedItem(
                flow="received",
                item_id=_WHIP_ID,
                item_name="Abyssal whip",
                quantity=quantity,
                unit_value=1_500_000,
            ),
        ),
    )


def test_the_slots_selector_is_passed_through_to_ranking(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips", params={"slots": 3})

    assert response.status_code == 200
    assert '<option value="3" selected>' in response.text


def test_a_slot_count_outside_the_real_ge_range_is_clamped(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips", params={"slots": 99})

    assert response.status_code == 200
    assert '<option value="8" selected>' in response.text


def test_a_zero_slot_count_is_clamped_up_rather_than_producing_no_offers(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips", params={"slots": 0})

    assert response.status_code == 200
    assert '<option value="1" selected>' in response.text


# -- prefilling from synced state --------------------------------------------------------------


def test_flips_page_defaults_to_ten_million_with_no_synced_state(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/flips")

    assert response.status_code == 200
    assert 'value="10000000"' in response.text
    assert "from your synced coins" not in response.text


def test_flips_page_prefills_cash_stack_from_synced_coins(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="char-1",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(
                LoadoutItem(item_id=995, item_name="Coins", quantity=42_000_000, unit_value=1),
            ),
            bank=(),
            skills={},
        )
    )

    with _patched_market():
        response = signed_in.get("/flips")

    assert response.status_code == 200
    assert 'value="42000000"' in response.text
    assert "from your synced coins" in response.text


def test_an_explicitly_typed_cash_stack_is_not_overridden_by_synced_coins(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="char-1",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(
                LoadoutItem(item_id=995, item_name="Coins", quantity=42_000_000, unit_value=1),
            ),
            bank=(),
            skills={},
        )
    )

    with _patched_market():
        response = signed_in.get("/flips", params={"cash_stack": 5_000_000})

    assert response.status_code == 200
    assert 'value="5000000"' in response.text
    assert "from your synced coins" not in response.text


# -- watching straight from a ranked row -----------------------------------------------------


def test_an_item_can_be_watched_by_id_from_a_ranked_row(signed_in: TestClient):
    """The toggles on the flip and alch tables are looking at a row the snapshot produced,
    so they name the id. Matching on the name instead would add a way for the exact item on
    screen to be misidentified."""
    with _patched_market():
        response = signed_in.post("/api/watchlist", json={"item_id": _WHIP_ID})

        assert response.status_code == 201
        assert response.json()["name"] == "Abyssal whip"
        assert "Abyssal whip" in signed_in.get("/watchlist").text


def test_watching_the_same_id_twice_is_refused(signed_in: TestClient):
    with _patched_market():
        signed_in.post("/api/watchlist", json={"item_id": _WHIP_ID})
        second = signed_in.post("/api/watchlist", json={"item_id": _WHIP_ID})

    assert second.status_code == 409


def test_an_id_the_market_has_never_heard_of_is_refused(signed_in: TestClient):
    with _patched_market():
        response = signed_in.post("/api/watchlist", json={"item_id": 999_999})

    assert response.status_code == 404


def test_the_flips_page_marks_an_item_already_watched(signed_in: TestClient):
    with _patched_market():
        signed_in.post("/api/watchlist", json={"item_id": _WHIP_ID})
        response = signed_in.get("/flips")

    assert "Watching" in response.text
