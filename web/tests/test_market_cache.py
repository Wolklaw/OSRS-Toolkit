from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.models import ItemMapping, MarketPoint

from toolkit_web.market_cache import MarketCache

_MAPPING = {1: ItemMapping(item_id=1, name="Whip", members=True, buy_limit=70, high_alch=100)}
_POINTS = [
    MarketPoint(item_id=1, high=100, low=90, high_time=1, low_time=1, volume_5m=10, volume_1h=100)
]


@pytest.fixture
def cache(tmp_path: Path) -> MarketCache:
    return MarketCache(tmp_path / "market-cache")


def test_the_first_read_fetches(cache: MarketCache):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=(_MAPPING, _POINTS),
    ) as fetch:
        mappings, points = cache.snapshot()

    fetch.assert_called_once()
    assert mappings == _MAPPING
    assert points == _POINTS


def test_a_second_read_within_the_window_does_not_fetch_again(cache: MarketCache):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=(_MAPPING, _POINTS),
    ) as fetch:
        cache.snapshot()
        cache.snapshot()

    assert fetch.call_count == 1


def test_a_read_past_the_window_fetches_again(cache: MarketCache):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=(_MAPPING, _POINTS),
    ) as fetch:
        cache.snapshot()
        with patch("toolkit_web.market_cache.time.monotonic", return_value=1_000_000.0):
            cache.snapshot()

    assert fetch.call_count == 2


def test_a_failed_refresh_falls_back_to_the_last_good_snapshot(cache: MarketCache):
    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=(_MAPPING, _POINTS),
    ):
        cache.snapshot()

    with (
        patch(
            "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
            side_effect=MarketDataError("wiki is down"),
        ),
        patch("toolkit_web.market_cache.time.monotonic", return_value=1_000_000.0),
    ):
        mappings, points = cache.snapshot()

    # Still the old data — a transient outage should not blank a page that was working.
    assert mappings == _MAPPING
    assert points == _POINTS


def test_a_failure_before_any_successful_fetch_has_nothing_to_fall_back_to(cache: MarketCache):
    with (
        patch(
            "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
            side_effect=MarketDataError("wiki is down"),
        ),
        pytest.raises(MarketDataError),
    ):
        cache.snapshot()


def test_a_failure_does_not_prevent_the_very_next_read_from_retrying(cache: MarketCache):
    """No cooldown after a failure — at this traffic scale a request is never frequent
    enough to risk hammering the wiki, and delaying recovery would only cost the first
    person to load a page after the outage clears."""
    with (
        patch(
            "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
            side_effect=MarketDataError("still down"),
        ),
        pytest.raises(MarketDataError),
    ):
        cache.snapshot()

    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_snapshot",
        return_value=(_MAPPING, _POINTS),
    ) as fetch:
        cache.snapshot()

    fetch.assert_called_once()
