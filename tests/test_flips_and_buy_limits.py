from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade
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
