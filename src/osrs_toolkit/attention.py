"""Detects when a tracked trade has just reached a state worth alerting on.

Kept Qt-free so "did this offer just finish?" can be tested without a window on screen.
"""

from __future__ import annotations

from collections.abc import Collection, Hashable, Mapping

# The buy is done and the goods are in hand — time to sell.
READY_TO_SELL_STATUS = "Bought"

# The other side of the trade: nothing left owed, but coins are still uncollected.
FLIP_CLOSED_STATUS = "Completed"

# Checked separately, not as one set: a position that both buys and sells between
# two polls should trigger both alerts, not have the second swallowed by the first.
JOURNAL_ALERT_STATUSES = (READY_TO_SELL_STATUS, FLIP_CLOSED_STATUS)


def newly_reached[Key: Hashable](
    previous: Mapping[Key, str] | None,
    current: Mapping[Key, str],
    states: Collection[str],
) -> frozenset[Key]:
    """Keys whose state just became one of ``states``.

    Returns nothing on the first call (``previous is None``), so a restart doesn't
    flash every offer that was already finished before the app opened. A key missing
    from ``previous`` counts as newly arrived — that covers a buy that fills instantly,
    or a slot the game re-sends on login already done.
    """
    if previous is None:
        return frozenset()
    return frozenset(
        key for key, state in current.items() if state in states and previous.get(key) not in states
    )


def journal_alert_positions(
    previous: Mapping[int, str] | None, current: Mapping[int, str]
) -> frozenset[int]:
    """Tracked positions that have just finished either side of their trade."""
    return frozenset().union(
        *(newly_reached(previous, current, (status,)) for status in JOURNAL_ALERT_STATUSES)
    )
