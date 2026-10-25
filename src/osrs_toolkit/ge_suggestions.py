from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from osrs_toolkit.journal import TrackedTrade
from osrs_toolkit.models import ItemMapping, MarketPoint
from osrs_toolkit.ranking import offer_targets

MAX_SUGGESTIONS = 16
BUYING_STATUSES = frozenset({"Pending buy"})
SELLING_STATUSES = frozenset({"Bought", "Listed for sale", "Partially sold"})


@dataclass(frozen=True, slots=True)
class GeSuggestion:
    item_id: int
    item_name: str
    side: str
    price: int
    quantity: int
    source: str


def position_suggestions(positions: Iterable[TrackedTrade]) -> list[GeSuggestion]:
    suggestions: list[GeSuggestion] = []
    for position in positions:
        if position.item_id is None:
            continue
        if position.status in BUYING_STATUSES:
            remaining = max(0, position.quantity - position.bought_quantity)
            if remaining > 0 and position.buy_suggestion > 0:
                suggestions.append(
                    GeSuggestion(
                        item_id=position.item_id,
                        item_name=position.item_name,
                        side="buy",
                        price=position.buy_suggestion,
                        quantity=remaining,
                        source="position",
                    )
                )
        elif position.status in SELLING_STATUSES:
            stock = position.unsold_stock
            if stock > 0 and position.asking_price > 0:
                suggestions.append(
                    GeSuggestion(
                        item_id=position.item_id,
                        item_name=position.item_name,
                        side="sell",
                        price=position.asking_price,
                        quantity=stock,
                        source="position",
                    )
                )
    return suggestions


def watchlist_suggestions(
    item_ids: Iterable[int],
    mappings: Mapping[int, ItemMapping],
    points: Mapping[int, MarketPoint],
    owned: Mapping[int, int],
    limit_room: Mapping[int, int],
) -> list[GeSuggestion]:
    suggestions: list[GeSuggestion] = []
    for item_id in item_ids:
        mapping = mappings.get(item_id)
        point = points.get(item_id)
        if mapping is None or point is None:
            continue
        try:
            buy_price, sell_price = offer_targets(point)
        except ValueError:
            continue
        room = limit_room.get(item_id, mapping.buy_limit)
        if room is None:
            room = 1
        if room > 0:
            suggestions.append(
                GeSuggestion(
                    item_id=item_id,
                    item_name=mapping.name,
                    side="buy",
                    price=buy_price,
                    quantity=room,
                    source="watchlist",
                )
            )
        held = owned.get(item_id, 0)
        if held > 0:
            suggestions.append(
                GeSuggestion(
                    item_id=item_id,
                    item_name=mapping.name,
                    side="sell",
                    price=sell_price,
                    quantity=held,
                    source="watchlist",
                )
            )
    return suggestions


def combine(
    from_positions: list[GeSuggestion], from_watchlist: list[GeSuggestion]
) -> list[GeSuggestion]:
    taken = {(suggestion.item_id, suggestion.side) for suggestion in from_positions}
    extra = [s for s in from_watchlist if (s.item_id, s.side) not in taken]
    return [*from_positions, *extra][:MAX_SUGGESTIONS]
