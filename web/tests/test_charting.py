from __future__ import annotations

from osrs_toolkit.models import TimeseriesPoint

from toolkit_web.charting import net_worth_history_svg, price_history_svg, volume_history_svg


def test_price_history_svg_is_empty_with_fewer_than_two_points():
    assert price_history_svg([]) == ""
    assert price_history_svg([TimeseriesPoint(timestamp=1, average_high=100, average_low=90)]) == ""


def test_price_history_svg_plots_both_lines():
    points = [
        TimeseriesPoint(timestamp=1_000, average_high=100, average_low=90),
        TimeseriesPoint(timestamp=2_000, average_high=110, average_low=95),
    ]

    svg = price_history_svg(points)

    assert svg.startswith("<svg")
    assert svg.count("<polyline") == 2


def test_price_history_svg_skips_points_with_no_price_at_all():
    points = [
        TimeseriesPoint(timestamp=1_000, average_high=None, average_low=None),
        TimeseriesPoint(timestamp=2_000, average_high=100, average_low=90),
        TimeseriesPoint(timestamp=3_000, average_high=110, average_low=95),
    ]

    svg = price_history_svg(points)

    assert svg.startswith("<svg")


def test_net_worth_history_svg_is_empty_with_fewer_than_two_points():
    assert net_worth_history_svg([]) == ""
    assert net_worth_history_svg([("2026-01-01", 100)]) == ""


def test_net_worth_history_svg_plots_a_line():
    points = [("2026-01-01", 1_000_000), ("2026-01-02", 5_000_000)]

    svg = net_worth_history_svg(points)

    assert svg.startswith("<svg")
    assert "1,000,000" in svg
    assert "5,000,000" in svg


def test_volume_history_svg_is_empty_with_fewer_than_two_points():
    assert volume_history_svg([]) == ""
    assert (
        volume_history_svg(
            [TimeseriesPoint(timestamp=1, average_high=100, average_low=90, high_volume=10)]
        )
        == ""
    )


def test_volume_history_svg_is_empty_when_every_interval_has_zero_volume():
    points = [
        TimeseriesPoint(timestamp=1_000, average_high=100, average_low=90),
        TimeseriesPoint(timestamp=2_000, average_high=110, average_low=95),
    ]

    assert volume_history_svg(points) == ""


def test_volume_history_svg_draws_a_bar_per_nonzero_interval():
    points = [
        TimeseriesPoint(
            timestamp=1_000, average_high=100, average_low=90, high_volume=50, low_volume=25
        ),
        TimeseriesPoint(
            timestamp=2_000, average_high=110, average_low=95, high_volume=0, low_volume=0
        ),
        TimeseriesPoint(
            timestamp=3_000, average_high=105, average_low=92, high_volume=10, low_volume=10
        ),
    ]

    svg = volume_history_svg(points)

    assert svg.startswith("<svg")
    assert svg.count("<rect") == 2  # the zero-volume interval draws no bar
    assert "75" in svg  # the max-volume axis label (50 + 25)
