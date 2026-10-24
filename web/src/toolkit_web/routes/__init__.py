from __future__ import annotations

ACTIVITY_WINDOW_DAYS = 90

POSITION_STATUSES = (
    "Pending buy",
    "Bought",
    "Listed for sale",
    "Partially sold",
    "Completed",
    "Cancelled",
    "Supplies",
)

CLOSED_STATUSES = frozenset({"Completed", "Cancelled", "Supplies"})
