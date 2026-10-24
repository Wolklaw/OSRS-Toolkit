"""Turning domain objects into what a template reads.

Nothing here touches a request, a session or the database. Keeping it apart from the
routing module is what makes it testable without an app, and keeps that module about HTTP.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from fastapi import HTTPException
from osrs_toolkit.buy_limits import buy_limit_status
from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint
from osrs_toolkit.ranking import ge_tax, is_tax_exempt, offer_targets

#: Excludes 0/O and 1/I/L — characters easy to mistype or misread on a phone screen or a
#: scrap of paper, which is where a code with no Discord app or email behind it has to
#: survive. 32 characters ^ 16 positions is comfortably beyond brute-forcing regardless.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

#: Marks an account id that came from a recovery code rather than from Discord. The profile
#: page is the one place that has to tell the two apart — a Discord name is Discord's to
#: change, so only a code account is offered a rename it would actually keep.
_RECOVERY_ID_PREFIX = "local:"

# What each status says to someone who arrived by clicking a link, rather than the bare
# ``detail`` a JSON client reads. Anything not here is a generic failure, which is honest:
# guessing at a friendly name for an unexpected code just invents reassurance.
ERROR_PAGES: dict[int, tuple[str, str]] = {
    400: ("That request didn't make sense", "Something about the address or form was off."),
    403: ("Not yours to open", "This belongs to a different account."),
    404: ("Nothing here", "There is no page at that address."),
    405: ("Wrong way in", "That address exists, but not for this kind of request."),
    422: ("That didn't add up", "One of the values sent with the request was not usable."),
    429: ("Too fast", "Give it a moment and try again."),
    500: ("Something broke", "That is this end's fault, not yours."),
    502: ("The other end didn't answer", "A service this page depends on is not responding."),
    503: ("Back shortly", "RuneScope is briefly unavailable."),
}


def _useful_detail(status_code: int, detail: object) -> str:
    """The route's own message, when it has one worth reading.

    Starlette fills ``detail`` with the status's reason phrase when a route gave none, and
    printing "Not Found" underneath "There is no page at that address" says it twice.
    """
    text = str(detail or "").strip()
    try:
        generic = HTTPStatus(status_code).phrase
    except ValueError:
        generic = ""
    return "" if text.casefold() == generic.casefold() else text


def _generate_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def _recovery_account_id(code: str) -> str:
    """The account id a recovery code maps to — never the code itself.

    Prefixed so it can never collide with a real Discord id, which is always a bare numeric
    snowflake. Normalized before hashing so a code pasted back without its dashes, in the
    wrong case, or with stray whitespace still finds the same account it was issued for.
    """
    normalized = "".join(ch for ch in code.upper() if ch.isalnum())
    return _RECOVERY_ID_PREFIX + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _optional_int(value: object, field: str) -> int | None:
    """A whole number, or ``None`` for a field the caller left out or deliberately cleared.

    JSON has no way to say "leave this alone" other than omitting it, and an empty form input
    arrives as an empty string rather than as nothing at all — both mean the same thing here.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number") from error


def _fill_list(value: object, field: str) -> list[tuple[int, int]] | None:
    """A list of (quantity, price) fills, or ``None`` when the caller sent none at all.

    ``None`` and ``[]`` are deliberately different: an absent list leaves whatever fills are
    already recorded untouched, while an empty one is an explicit "this position has no fills"
    that clears them. The journal draws the same distinction, so passing it straight through
    is what keeps a synced position's history from being erased by an unrelated edit.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail=f"{field} must be a list of fills")
    fills: list[tuple[int, int]] = []
    for entry in value:
        if not isinstance(entry, list | tuple) or len(entry) != 2:
            raise HTTPException(
                status_code=422, detail=f"Each {field} entry must be a quantity and a price."
            )
        try:
            fills.append((int(entry[0]), int(entry[1])))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422, detail=f"{field} quantities and prices must be whole numbers"
            ) from error
    return fills


#: How long after a desktop token was last used the desktop app still counts as connected.
#: It polls once a minute and ``last_used_at`` is only rewritten once a minute, so the window
#: has to be several times that or a perfectly healthy app would blink out between writes.
DESKTOP_CONNECTED_SECONDS = 5 * 60


def _desktop_activity(tokens: list) -> tuple[str, str | None]:
    """(state, last_seen) for the desktop app, from the tokens issued to this account.

    The same distinction the plugin gets: a token that exists is not an app that is running.
    ``"waiting"`` is a token that was minted and never used — almost always somebody who
    copied it and has not pasted it in yet, which is worth saying rather than showing them a
    connected badge for an app that has never once called.
    """
    if not tokens:
        return "none", None
    seen = [token.last_used_at for token in tokens if token.last_used_at]
    if not seen:
        return "waiting", None
    last = max(seen)
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return "waiting", None
    return ("live" if age <= DESKTOP_CONNECTED_SECONDS else "idle"), last


def _net_worth_composition(snapshot: object, total_invested: int) -> list[dict]:
    """What the dashboard's net-worth figure is actually made of, right now — not history,
    just the latest snapshot split into cash, carried gear, bank, and capital tied up in open
    Grand Exchange positions. Rows with nothing in them are dropped rather than shown at 0%,
    since an account with no bank snapshot yet still has open positions worth showing alone.
    """
    cash = equipment_value = inventory_value = bank_value = 0
    if snapshot is not None:
        cash = snapshot.quantity_owned(995)
        equipment_value = sum(
            item.total_value for item in snapshot.equipment if item.item_id != 995
        )
        inventory_value = sum(
            item.total_value for item in snapshot.inventory if item.item_id != 995
        )
        bank_value = sum(item.total_value for item in snapshot.bank if item.item_id != 995)
    buckets = [
        ("Cash", cash, "--good"),
        ("Carried gear", equipment_value + inventory_value, "--accent"),
        ("Bank", bank_value, "--info"),
        ("GE in-flight", total_invested, "--border-strong"),
    ]
    total = sum(value for _label, value, _color in buckets)
    if total <= 0:
        return []
    return [
        {
            "label": label,
            "value": value,
            "percent": value / total * 100,
            "color": color,
        }
        for label, value, color in buckets
        if value > 0
    ]


def _buy_limit_room(
    repository: JournalRepository,
    mappings: dict[int, ItemMapping],
    account_hash: str | None,
    now: datetime,
) -> dict[int, int]:
    """How much of each item's 4-hour buy limit this character still has free.

    Only items actually bought inside the window appear, which is exactly the shape
    ``rank_flips`` wants: an item nobody has touched is absent and stays unconstrained,
    rather than needing a row saying "all of it".
    """
    limits = {item.item_id: item.buy_limit for item in mappings.values() if item.buy_limit}
    recent_fills = repository.list_synced_trades(
        event_type="ge_fill",
        since=now - timedelta(hours=4),
        account_hash=account_hash,
    )
    return {
        status.item_id: status.remaining for status in buy_limit_status(recent_fills, limits, now)
    }


def _market_breakdown(item: ItemMapping, point: MarketPoint, now: int) -> dict:
    """Every number the desktop window's item-details dialog shows, for one item.

    Assembled here rather than in the template so the page stays a list of already-decided
    values — the arithmetic that turns a raw market point into a margin belongs next to the
    ranking code that uses the same rules, not inside a Jinja expression.
    """
    buy_price, sell_price = offer_targets(point)
    tax = ge_tax(sell_price, exempt=is_tax_exempt(item.item_id, item.name))
    profit = sell_price - buy_price - tax
    return {
        "buy_price": buy_price,
        "sell_price": sell_price,
        "tax": tax,
        "profit": profit,
        "roi": profit / buy_price * 100 if buy_price else 0.0,
        "latest_low": point.low,
        "latest_high": point.high,
        "average_low_5m": point.average_low_5m,
        "average_high_5m": point.average_high_5m,
        "average_low_1h": point.average_low_1h,
        "average_high_1h": point.average_high_1h,
        "volume_5m": point.volume_5m,
        "volume_1h": point.volume_1h,
        "buy_limit": item.buy_limit,
        "high_alch": item.high_alch,
        "members": item.members,
        "age_minutes": max(0, now - min(point.high_time, point.low_time)) // 60,
    }


_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _history_stats(history: list[TimeseriesPoint]) -> tuple[dict | None, list[dict]]:
    """Two views of the same timeseries the price chart already has: average spread and
    volume across the window, and average volume by day of week — a seasonality flag, not
    a forecast. Both empty for too little history to say anything from."""
    if not history:
        return None, []
    spreads = [
        point.average_high - point.average_low
        for point in history
        if point.average_high is not None and point.average_low is not None
    ]
    total_volume = sum(point.total_volume for point in history)
    stats = {
        "average_spread": round(sum(spreads) / len(spreads)) if spreads else None,
        "total_volume": total_volume,
        "average_volume": round(total_volume / len(history)),
    }
    volumes_by_weekday: dict[int, list[int]] = {}
    for point in history:
        weekday = datetime.fromtimestamp(point.timestamp, tz=UTC).weekday()
        volumes_by_weekday.setdefault(weekday, []).append(point.total_volume)
    by_weekday = [
        {
            "day": _WEEKDAY_NAMES[weekday],
            "average_volume": round(sum(volumes) / len(volumes)),
        }
        for weekday, volumes in sorted(volumes_by_weekday.items())
    ]
    return stats, by_weekday
