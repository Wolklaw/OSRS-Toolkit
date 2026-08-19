"""The live price lookup MainWindow builds for the sync importer.

``MainWindow._suggested_sell_prices`` turns the market snapshot already loaded for the
Watchlist and GE Flipper into a plain ``{item_id: sell_price}`` map, so a buy fill or offer
with nothing pre-tracked can still seed a real profit estimate (see test_journal.py and
test_runelite_sync.py for the journal side of that).
"""

from __future__ import annotations

from osrs_toolkit.app import MainWindow
from osrs_toolkit.models import MarketPoint
from osrs_toolkit.ranking import offer_targets


def _point(item_id: int, **overrides: object) -> MarketPoint:
    base: dict[str, object] = {
        "item_id": item_id,
        "high": 120,
        "low": 100,
        "high_time": 1_000,
        "low_time": 1_000,
        "volume_5m": 50,
        "volume_1h": 500,
    }
    base.update(overrides)
    return MarketPoint(**base)  # type: ignore[arg-type]


def test_maps_each_point_to_its_passive_sell_target(window: MainWindow) -> None:
    window._points = [_point(1), _point(2, high=250, low=200)]

    prices = window._suggested_sell_prices()

    assert prices == {
        1: offer_targets(window._points[0])[1],
        2: offer_targets(window._points[1])[1],
    }


def test_empty_before_the_first_market_load(window: MainWindow) -> None:
    window._points = []
    assert window._suggested_sell_prices() == {}


def test_skips_a_point_with_no_usable_observation_on_either_side(window: MainWindow) -> None:
    """A point with zero prices on both sides (e.g. an item with no trade history) makes
    offer_targets raise; that one item is skipped rather than the whole lookup failing."""
    dead = _point(
        9,
        high=0,
        low=0,
        average_high_5m=None,
        average_low_5m=None,
        average_high_1h=None,
        average_low_1h=None,
    )
    window._points = [_point(1), dead]

    prices = window._suggested_sell_prices()

    assert set(prices) == {1}
