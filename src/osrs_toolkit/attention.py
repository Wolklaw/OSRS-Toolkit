from __future__ import annotations

from collections.abc import Collection, Hashable, Mapping

READY_TO_SELL_STATUS = "Bought"

FLIP_CLOSED_STATUS = "Completed"

JOURNAL_ALERT_STATUSES = (READY_TO_SELL_STATUS, FLIP_CLOSED_STATUS)


def newly_reached[Key: Hashable](
    previous: Mapping[Key, str] | None,
    current: Mapping[Key, str],
    states: Collection[str],
) -> frozenset[Key]:
    if previous is None:
        return frozenset()
    return frozenset(
        key for key, state in current.items() if state in states and previous.get(key) not in states
    )


def journal_alert_positions(
    previous: Mapping[int, str] | None, current: Mapping[int, str]
) -> frozenset[int]:
    return frozenset().union(
        *(newly_reached(previous, current, (status,)) for status in JOURNAL_ALERT_STATUSES)
    )
