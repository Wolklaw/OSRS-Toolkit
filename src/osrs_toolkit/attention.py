"""What has just changed, and is therefore worth pointing the player at.

The app learns about a finished buy the same way it learns about everything else: by
re-reading the journal and the plugin's slot file every few seconds and finding them
different from last time. "Different from last time" is the whole of the logic here, kept
away from the widgets so the question "did this offer just finish?" can be asked — and
tested — without a window on screen.
"""

from __future__ import annotations

from collections.abc import Collection, Hashable, Mapping

#: The buy is done and the goods are in hand: the moment the player turns around and
#: starts selling, and the one this module exists for.
READY_TO_SELL_STATUS = "Bought"

#: The other end of the same trade. Nothing more is owed to it, but the coins are still
#: sitting in a Grand Exchange slot waiting to be collected.
FLIP_CLOSED_STATUS = "Completed"

#: Each is looked for on its own rather than as one set of two: a position that buys and
#: sells between two observations reaches both, and asking for them together would let
#: the first swallow the second — "Bought" would already count as arrived, so the
#: "Completed" that followed it would go unannounced.
JOURNAL_ALERT_STATUSES = (READY_TO_SELL_STATUS, FLIP_CLOSED_STATUS)


def newly_reached[Key: Hashable](
    previous: Mapping[Key, str] | None,
    current: Mapping[Key, str],
    states: Collection[str],
) -> frozenset[Key]:
    """Keys whose state has just become one of ``states``.

    ``previous`` is None on the first look, when there is no "just" to speak of: every
    state seen is simply the state things were already in, so nothing is announced. That
    is what keeps a restart from flashing every finished offer the player left behind.

    A key absent from ``previous`` counts as newly arrived, because it is — an untracked
    buy that fills completely creates its journal row already finished, and a slot the
    game re-sends on login can arrive with its offer already done. A key that was in one
    of ``states`` and has moved to another of them is not announced again; pass the states
    separately when each deserves its own mention.
    """
    if previous is None:
        return frozenset()
    return frozenset(
        key
        for key, state in current.items()
        if state in states and previous.get(key) not in states
    )


def journal_alert_positions(
    previous: Mapping[int, str] | None, current: Mapping[int, str]
) -> frozenset[int]:
    """Tracked positions that have just finished a side of their trade.

    Both ends are worth catching. Finishing the buy is the one the player has to act on
    here — the item now needs listing, and finding it again in a long journal is the
    chore this saves. Finishing the sale needs nothing of the journal, but the coins are
    still uncollected in-game, so the row saying so is worth the same glance.
    """
    return frozenset().union(
        *(newly_reached(previous, current, (status,)) for status in JOURNAL_ALERT_STATUSES)
    )
