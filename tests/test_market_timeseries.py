"""Parsing the wiki's /timeseries response, independent of the network call itself."""

from __future__ import annotations

from pathlib import Path

from osrs_toolkit.market import WikiMarketClient, _parse_timeseries


def test_a_normal_entry_parses_both_prices() -> None:
    payload = {"data": [{"timestamp": 1_700_000_000, "avgHighPrice": 210, "avgLowPrice": 195}]}

    (point,) = _parse_timeseries(payload)

    assert point.timestamp == 1_700_000_000
    assert point.average_high == 210
    assert point.average_low == 195


def test_an_interval_with_no_trades_keeps_its_timestamp_with_null_prices() -> None:
    """A gap in trading is real information — dropping the point would hide it."""
    payload = {"data": [{"timestamp": 1_700_000_000, "avgHighPrice": None, "avgLowPrice": None}]}

    (point,) = _parse_timeseries(payload)

    assert point.average_high is None
    assert point.average_low is None


def test_an_entry_missing_its_timestamp_is_dropped() -> None:
    payload = {"data": [{"avgHighPrice": 210, "avgLowPrice": 195}]}

    assert _parse_timeseries(payload) == []


def test_a_zero_or_negative_price_is_treated_as_unavailable() -> None:
    payload = {"data": [{"timestamp": 1_700_000_000, "avgHighPrice": 0, "avgLowPrice": -5}]}

    (point,) = _parse_timeseries(payload)

    assert point.average_high is None
    assert point.average_low is None


def test_an_unexpected_payload_shape_yields_no_points() -> None:
    assert _parse_timeseries({}) == []
    assert _parse_timeseries([]) == []
    assert _parse_timeseries({"data": "not a list"}) == []


def test_points_preserve_the_response_order() -> None:
    payload = {
        "data": [
            {"timestamp": 100, "avgHighPrice": 10, "avgLowPrice": 9},
            {"timestamp": 200, "avgHighPrice": 11, "avgLowPrice": 10},
        ]
    }

    points = _parse_timeseries(payload)

    assert [point.timestamp for point in points] == [100, 200]


def test_fetch_timeseries_caches_under_a_filesystem_safe_key(tmp_path: Path) -> None:
    """The route itself contains "?" and "&", which are not legal in a Windows filename."""
    client = WikiMarketClient(cache_dir=tmp_path)
    seen_routes: list[str] = []

    def fake_get(route: str, *, cache_key: str | None = None):
        seen_routes.append(route)
        assert cache_key == "timeseries_6h_1234"
        return {"data": [{"timestamp": 1, "avgHighPrice": 5, "avgLowPrice": 4}]}

    client._get = fake_get  # type: ignore[method-assign]
    points = client.fetch_timeseries(1234, "6h")

    assert seen_routes == ["timeseries?timestep=6h&id=1234"]
    assert points[0].average_high == 5
