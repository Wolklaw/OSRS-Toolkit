"""How fresh the shared price snapshot is, refreshing it on demand, and what a stale ask
looks like once the market has moved past a listing.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.models import ItemMapping, MarketPoint

from toolkit_web.config import Settings

_WHIP_ID = 4151


def _snapshot(high: int = 1_600_000, low: int = 1_500_000):
    now = int(time.time())
    mapping = {
        _WHIP_ID: ItemMapping(
            item_id=_WHIP_ID, name="Abyssal whip", members=True, buy_limit=70, high_alch=100_000
        )
    }
    point = MarketPoint(
        item_id=_WHIP_ID,
        high=high,
        low=low,
        high_time=now,
        low_time=now,
        volume_5m=200,
        volume_1h=2_000,
        average_5m=(high + low) // 2,
        average_1h=(high + low) // 2,
        average_high_5m=high,
        average_low_5m=low,
        average_high_1h=high,
        average_low_1h=low,
        high_volume_5m=100,
        low_volume_5m=100,
        high_volume_1h=1_000,
        low_volume_1h=1_000,
    )
    return mapping, [point]


def _patched_market(**kwargs):
    return patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=_snapshot(**kwargs),
    )


# -- freshness ------------------------------------------------------------------------------


def test_the_market_age_is_unknown_until_something_has_been_fetched(client: TestClient):
    assert client.get("/api/market").json()["age_seconds"] is None


def test_the_market_age_becomes_a_number_once_a_snapshot_lands(signed_in: TestClient):
    with _patched_market():
        signed_in.get("/flips")

    age = signed_in.get("/api/market").json()["age_seconds"]
    assert age is not None
    assert age >= 0


def test_refreshing_fetches_again_rather_than_waiting_out_the_window(signed_in: TestClient):
    with _patched_market() as fetch:
        signed_in.get("/flips")
        calls_after_first_page = fetch.call_count

        signed_in.post("/api/market/refresh")

        assert fetch.call_count == calls_after_first_page + 1


def test_refreshing_reports_an_unreachable_wiki_rather_than_pretending(client: TestClient):
    """Signed in, but the very first fetch fails — there is no held snapshot to fall back on,
    so the honest answer is that it could not be done."""
    client.post("/auth/dev-login", json={"discord_user_id": "111", "username": "Wolklaw"})

    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        side_effect=MarketDataError("offline"),
    ):
        response = client.post("/api/market/refresh")

    assert response.status_code == 502


def test_refreshing_needs_a_session(client: TestClient):
    """Not because prices are private — because an endpoint anyone can hit is a way to make
    this server call the wiki over and over."""
    assert client.post("/api/market/refresh").status_code == 401


# -- stale asks -----------------------------------------------------------------------------


def test_a_listing_the_market_has_moved_past_is_flagged(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        _WHIP_ID, "Abyssal whip", 1, target_buy=1_400_000, target_sell=1_600_000
    )
    repository.update_tracked(position_id, "Listed for sale", 1_400_000, None)

    # The market's own passive sell target is now the 1h/5m instant-buy average of
    # 1,500,000 — over 6% below the 1,600,000 this position is still asking.
    with _patched_market(high=1_500_000, low=1_400_000):
        response = signed_in.get("/")

    assert "Stale ask" in response.text


def test_a_listing_still_at_the_market_price_is_not_flagged(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        _WHIP_ID, "Abyssal whip", 1, target_buy=1_500_000, target_sell=1_600_000
    )
    repository.update_tracked(position_id, "Listed for sale", 1_500_000, None)

    with _patched_market(high=1_600_000, low=1_500_000):
        response = signed_in.get("/")

    assert "Stale ask" not in response.text


def test_the_dashboard_still_renders_with_no_prices_to_grade_against(signed_in: TestClient):
    """Nothing gets flagged, rather than the page failing over a market it could not reach."""
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/")

    assert response.status_code == 200
    assert "Stale ask" not in response.text
