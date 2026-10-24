"""The HTTP surface, one module per group of routes.

Each module exposes ``build(deps) -> APIRouter``; ``create_app`` includes them in order.
The vocabulary more than one of them needs lives here rather than in any single module, so
that importing a page does not drag in the router that happens to define a constant.
"""

from __future__ import annotations

#: Only the recent activity feed shows on the dashboard, so nothing has a reason to read
#: further back than this in one request — and unlike positions, synced-trade rows are never
#: revisited once shown, so there is no correctness reason to load more.
ACTIVITY_WINDOW_DAYS = 90

#: The statuses a position can be moved between by hand, in the order a flip walks through
#: them. Kept in step with the desktop window's own Update dialog, which offers exactly these
#: — anything else would let the web front end write a status the desktop cannot display.
POSITION_STATUSES = (
    "Pending buy",
    "Bought",
    "Listed for sale",
    "Partially sold",
    "Completed",
    "Cancelled",
    "Supplies",
)

#: Statuses that are no longer money working in the market. "Supplies" belongs here with the
#: two obvious ones: those goods were bought to be used, not resold, so counting them as
#: capital still committed to a flip would overstate what is actually out there working.
CLOSED_STATUSES = frozenset({"Completed", "Cancelled", "Supplies"})
