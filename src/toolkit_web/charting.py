"""A hand-rolled inline SVG line chart for one item's price history.

No charting library: this app has no JS build step and no CDN to pull one from, and a couple
of polylines is little enough code that pulling in a dependency for it would cost more than it
saves. Values are plotted, not styled with CSS variables — an SVG downloaded or viewed outside
the page has no ``:root`` to read them from, so the same handful of hex colours the rest of the
app uses are written here directly instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

from osrs_toolkit.models import TimeseriesPoint

_HIGH_COLOR = "#d5ad52"
_LOW_COLOR = "#5fb0e2"
_GRID_COLOR = "#2a2e38"
_TEXT_COLOR = "#8b8f9a"


def price_history_svg(points: list[TimeseriesPoint], width: int = 760, height: int = 240) -> str:
    """An SVG chart of average high/low prices over time, oldest first. Empty string when
    there is nothing plottable — a caller decides what to show in that case, this does not
    invent an empty chart to say so."""
    plotted = [
        (point.timestamp, point.average_high, point.average_low)
        for point in points
        if point.average_high is not None or point.average_low is not None
    ]
    if len(plotted) < 2:
        return ""

    all_prices = [
        price
        for _timestamp, high, low in plotted
        for price in (high, low)
        if price is not None
    ]
    min_price, max_price = min(all_prices), max(all_prices)
    price_span = max(max_price - min_price, 1)
    min_time = plotted[0][0]
    max_time = plotted[-1][0]
    time_span = max(max_time - min_time, 1)

    padding_left, padding_right, padding_top, padding_bottom = 70, 20, 20, 30
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom

    def x_for(timestamp: int) -> float:
        return padding_left + (timestamp - min_time) / time_span * plot_width

    def y_for(price: int) -> float:
        return padding_top + (1 - (price - min_price) / price_span) * plot_height

    def polyline(key: int) -> str:
        coords = " ".join(
            f"{x_for(timestamp):.1f},{y_for(price):.1f}"
            for timestamp, high, low in plotted
            if (price := (high if key == 0 else low)) is not None
        )
        return coords

    high_points = polyline(0)
    low_points = polyline(1)
    start_label = datetime.fromtimestamp(min_time, tz=UTC).strftime("%b %d")
    end_label = datetime.fromtimestamp(max_time, tz=UTC).strftime("%b %d")

    lines = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Segoe UI, system-ui, sans-serif" font-size="12">',
        f'<line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" '
        f'y2="{height - padding_bottom}" stroke="{_GRID_COLOR}"/>',
        f'<line x1="{padding_left}" y1="{height - padding_bottom}" x2="{width - padding_right}" '
        f'y2="{height - padding_bottom}" stroke="{_GRID_COLOR}"/>',
        f'<text x="{padding_left - 10}" y="{padding_top + 4}" fill="{_TEXT_COLOR}" '
        f'text-anchor="end">{max_price:,}</text>',
        f'<text x="{padding_left - 10}" y="{height - padding_bottom}" fill="{_TEXT_COLOR}" '
        f'text-anchor="end">{min_price:,}</text>',
        f'<text x="{padding_left}" y="{height - 8}" fill="{_TEXT_COLOR}">{start_label}</text>',
        f'<text x="{width - padding_right}" y="{height - 8}" fill="{_TEXT_COLOR}" '
        f'text-anchor="end">{end_label}</text>',
    ]
    if high_points:
        lines.append(
            f'<polyline points="{high_points}" fill="none" stroke="{_HIGH_COLOR}" '
            f'stroke-width="2"/>'
        )
    if low_points:
        lines.append(
            f'<polyline points="{low_points}" fill="none" stroke="{_LOW_COLOR}" '
            f'stroke-width="2"/>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def net_worth_history_svg(
    points: list[tuple[str, int]], width: int = 760, height: int = 200
) -> str:
    """The same chart shape as ``price_history_svg``, for a single series of
    ``(captured_at, total_value)`` readings instead of an item's two-sided price."""
    if len(points) < 2:
        return ""
    values = [value for _captured_at, value in points]
    min_value, max_value = min(values), max(values)
    value_span = max(max_value - min_value, 1)

    padding_left, padding_right, padding_top, padding_bottom = 80, 20, 20, 30
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom
    count = len(points)

    def x_for(index: int) -> float:
        return padding_left + (index / (count - 1)) * plot_width

    def y_for(value: int) -> float:
        return padding_top + (1 - (value - min_value) / value_span) * plot_height

    coords = " ".join(
        f"{x_for(index):.1f},{y_for(value):.1f}" for index, (_captured_at, value) in enumerate(points)
    )
    start_label = points[0][0][:10]
    end_label = points[-1][0][:10]

    return "\n".join(
        [
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, system-ui, sans-serif" font-size="12">',
            f'<line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" '
            f'y2="{height - padding_bottom}" stroke="{_GRID_COLOR}"/>',
            f'<line x1="{padding_left}" y1="{height - padding_bottom}" '
            f'x2="{width - padding_right}" y2="{height - padding_bottom}" stroke="{_GRID_COLOR}"/>',
            f'<text x="{padding_left - 10}" y="{padding_top + 4}" fill="{_TEXT_COLOR}" '
            f'text-anchor="end">{max_value:,}</text>',
            f'<text x="{padding_left - 10}" y="{height - padding_bottom}" fill="{_TEXT_COLOR}" '
            f'text-anchor="end">{min_value:,}</text>',
            f'<text x="{padding_left}" y="{height - 8}" fill="{_TEXT_COLOR}">{start_label}</text>',
            f'<text x="{width - padding_right}" y="{height - 8}" fill="{_TEXT_COLOR}" '
            f'text-anchor="end">{end_label}</text>',
            f'<polyline points="{coords}" fill="none" stroke="{_HIGH_COLOR}" stroke-width="2"/>',
            "</svg>",
        ]
    )
