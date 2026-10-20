"""Item history, Alch Finder and Skilling Profit render usefully signed out — they are pure
market-data calculators with no journal dependency, unlike the plugin-fed pages next to them.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.models import ItemMapping, MarketPoint

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
    )
    return mapping, [point]


def _patched_market():
    return patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=_market_snapshot(),
    )


def test_the_item_page_reads_signed_out(client: TestClient):
    with _patched_market():
        response = client.get(f"/item/{_WHIP_ID}")

    assert response.status_code == 200
    assert "Abyssal whip" in response.text
    assert "Sign in with Discord" not in response.text


def test_the_item_page_watch_button_reads_unwatched_when_signed_out(client: TestClient):
    with _patched_market():
        response = client.get(f"/item/{_WHIP_ID}")

    assert response.status_code == 200
    assert ">Watch<" in response.text


def test_the_alch_page_reads_signed_out(client: TestClient):
    with _patched_market():
        response = client.get("/alch")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_the_skilling_page_reads_signed_out(client: TestClient):
    with _patched_market():
        response = client.get("/skilling")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text


def test_the_buy_limits_page_still_needs_a_session(client: TestClient):
    """Unlike the three above, this one reads a personal purchase history — there is no
    signed-out version of "what have you bought in the last four hours"."""
    assert client.get("/buy-limits").status_code == 401


def test_a_signed_in_page_is_marked_uncacheable(signed_in: TestClient):
    """Every signed-in page is account-specific. Without this header nothing in the response
    would stop a cache rule covering HTML from serving one account's dashboard to another."""
    with _patched_market():
        response = signed_in.get("/")

    assert response.headers["cache-control"] == "private, no-store"


def test_a_signed_out_page_is_not_marked_private(client: TestClient):
    with _patched_market():
        response = client.get("/about")

    assert response.headers.get("cache-control") != "private, no-store"
