from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.account import AccountLookupError, PlayerProfile
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.models import ItemMapping, MarketPoint

_NATURE_RUNE_ID = 561
_CHEAP_ALCHABLE_ID = 999_999


def _alch_snapshot():
    now = int(time.time())

    def point(item_id: int, price: int, volume: int) -> MarketPoint:
        return MarketPoint(
            item_id=item_id,
            high=price,
            low=price,
            high_time=now,
            low_time=now,
            volume_5m=volume,
            volume_1h=volume,
            average_5m=price,
            average_1h=price,
            average_high_5m=price,
            average_low_5m=price,
            average_high_1h=price,
            average_low_1h=price,
            high_volume_5m=volume,
            low_volume_5m=volume,
            high_volume_1h=volume,
            low_volume_1h=volume,
        )

    mappings = {
        _CHEAP_ALCHABLE_ID: ItemMapping(
            item_id=_CHEAP_ALCHABLE_ID,
            name="Cheap alchable",
            members=False,
            buy_limit=100,
            high_alch=1_000,
        ),
    }
    points = [point(_NATURE_RUNE_ID, 180, 1_000), point(_CHEAP_ALCHABLE_ID, 50, 1_000)]
    return mappings, points


def _patched_market():
    return patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=_alch_snapshot(),
    )


def test_the_alch_page_shows_a_profitable_candidate(signed_in: TestClient):
    with _patched_market():
        response = signed_in.get("/alch")

    assert response.status_code == 200
    assert "Cheap alchable" in response.text


def test_the_alch_page_degrades_gracefully_when_the_wiki_is_unreachable(
    signed_in: TestClient,
):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/alch")

    assert response.status_code == 200
    assert "couldn't be reached" in response.text


def test_the_skilling_page_shows_a_lookup_error_for_an_unknown_character(
    signed_in: TestClient,
):
    with patch(
        "toolkit_web.app.fetch_player",
        side_effect=AccountLookupError("Character was not found on the OSRS hiscores."),
    ):
        response = signed_in.get("/skilling", params={"character": "nobody12345"})

    assert response.status_code == 200
    assert "was not found" in response.text


def test_the_skilling_page_marks_methods_eligible_once_looked_up(signed_in: TestClient):
    profile = PlayerProfile(name="Wolklaw", skills={"Overall": 500, "Cooking": 99})
    with (
        patch("toolkit_web.app.fetch_player", return_value=profile),
        _patched_market(),
    ):
        response = signed_in.get("/skilling", params={"character": "Wolklaw"})

    assert response.status_code == 200
    assert "Undefined" not in response.text
