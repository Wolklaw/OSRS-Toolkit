from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from osrs_toolkit.models import TimeseriesPoint

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True, slots=True)
class HistoryStats:
    average_spread: int | None
    total_volume: int
    average_volume: int


@dataclass(frozen=True, slots=True)
class WeekdayVolume:
    day: str
    average_volume: int


def history_stats(
    history: list[TimeseriesPoint],
) -> tuple[HistoryStats | None, list[WeekdayVolume]]:
    if not history:
        return None, []
    spreads = [
        point.average_high - point.average_low
        for point in history
        if point.average_high is not None and point.average_low is not None
    ]
    total_volume = sum(point.total_volume for point in history)
    stats = HistoryStats(
        average_spread=round(sum(spreads) / len(spreads)) if spreads else None,
        total_volume=total_volume,
        average_volume=round(total_volume / len(history)),
    )
    volumes_by_weekday: dict[int, list[int]] = {}
    for point in history:
        weekday = datetime.fromtimestamp(point.timestamp, tz=UTC).weekday()
        volumes_by_weekday.setdefault(weekday, []).append(point.total_volume)
    by_weekday = [
        WeekdayVolume(WEEKDAY_NAMES[weekday], round(sum(volumes) / len(volumes)))
        for weekday, volumes in sorted(volumes_by_weekday.items())
    ]
    return stats, by_weekday
