from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ItemMapping:
    item_id: int
    name: str
    members: bool
    buy_limit: int | None
    high_alch: int | None


@dataclass(frozen=True, slots=True)
class MarketPoint:
    item_id: int
    high: int
    low: int
    high_time: int
    low_time: int
    volume_5m: int
    volume_1h: int
    average_5m: float | None = None
    average_1h: float | None = None
    average_high_5m: int | None = None
    average_low_5m: int | None = None
    average_high_1h: int | None = None
    average_low_1h: int | None = None
    high_volume_5m: int = 0
    low_volume_5m: int = 0
    high_volume_1h: int = 0
    low_volume_1h: int = 0


@dataclass(frozen=True, slots=True)
class TimeseriesPoint:
    timestamp: int
    average_high: int | None
    average_low: int | None
    high_volume: int = 0
    low_volume: int = 0

    @property
    def total_volume(self) -> int:
        return self.high_volume + self.low_volume


@dataclass(frozen=True, slots=True)
class FlipCandidate:
    item_id: int
    name: str
    buy_price: int
    sell_price: int
    tax: int
    profit_each: int
    roi: float
    hourly_volume: int
    projected_volume: int
    buy_limit: int | None
    suggested_quantity: int
    capital_required: int
    potential_profit: int
    confidence: int
    age_seconds: int
    score: float
