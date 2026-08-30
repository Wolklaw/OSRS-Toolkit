"""HTTP surface.

Sign-in is Discord OAuth and nothing else — no password, no email address held anywhere. A
signed session cookie carries a Discord user id; everything past that reads or writes that
user's own journal, importing from the sync service first so a page is never more than one
request stale. See ``toolkit_web.importing`` for why that is a request-time step rather than a
background worker.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from osrs_toolkit.account import AccountLookupError, fetch_player
from osrs_toolkit.buy_limits import buy_limit_status
from osrs_toolkit.calculators import ALCH_POLICIES, alch_candidates, skill_results
from osrs_toolkit.csv_export import journal_csv
from osrs_toolkit.formatting import compact_items, synced_trade_label
from osrs_toolkit.journal_presentation import PERIOD_FILTERS, trade_needs_attention
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint
from osrs_toolkit.performance import by_item, by_strategy, calibration, realized_results, summarize
from osrs_toolkit.pvm import assess_all, estimate_gp_per_hour, observed_gp_per_hour
from osrs_toolkit.ranking import STRATEGIES, ge_tax, offer_targets, plan_flip_portfolio, rank_flips
from osrs_toolkit.runelite_sync import ge_offer_status_label
from osrs_toolkit.savings_goal import (
    SavingsProgress,
    daily_profit_rate,
    estimate_days_remaining,
    realized_profit_since,
)
from osrs_toolkit.supplies_report import supplies_spend_rows, total_supplies_spend

from toolkit_web import discord_oauth, turnstile
from toolkit_web.accounts_db import Account, AccountsDB
from toolkit_web.charting import net_worth_history_svg, price_history_svg, volume_history_svg
from toolkit_web.config import Settings
from toolkit_web.discord_webhook import is_discord_webhook_url, send_webhook_message
from toolkit_web.importing import (
    journal_for,
    known_characters,
    live_offer_state,
    plugin_activity,
    raw_sync_state,
    stored_journal,
)
from toolkit_web.market_cache import MarketCache
from toolkit_web.releases import RELEASES_URL, ReleaseCache
from toolkit_web.serialize import serialize
from toolkit_web.sessions import COOKIE_NAME, MAX_AGE_SECONDS, SessionSigner
from toolkit_web.token_probe import mint_pairing_token, probe_token, revoke_pairing_token

STATIC_DIR = Path(__file__).parent / "static"

STATE_COOKIE = "oauth_state"

#: The companion plugin's own repository. Since the Plugin Hub merged it on 2026-08-25 nobody
#: installs from here -- the Hub does that -- so this is a "read what you are installing" link
#: rather than a step anybody has to follow.
PLUGIN_REPO_URL = "https://github.com/Wolklaw/osrs-toolkit-runelite"

#: Which branch that link points at. Kept as its own constant even though only one URL is built
#: from it now, because this went wrong once already: until 2026-08-23 the repo's default branch
#: was still the pre-web plugin -- no ``SyncClient``, and neither the Pairing token nor the
#: master-switch setting the guide tells people to fill in -- so a source link handed somebody a
#: plugin that could not reach this site at all.
PLUGIN_BRANCH = "main"
PLUGIN_SOURCE_URL = f"{PLUGIN_REPO_URL}/tree/{PLUGIN_BRANCH}"

#: The plugin's name on the Plugin Hub, which is what people type into its search box. Matches
#: the manifest filename in runelite/plugin-hub, not the display name RuneLite shows.
PLUGIN_HUB_NAME = "osrs-toolkit-sync"
#: Which synced character's data to show. Plain, not signed: it names nothing but an
#: account_hash the caller's own pairing token already has full read access to, so there is
#: nothing here worth protecting from the browser that couldn't already be read straight from
#: the API. "" means "every character" and is written back by the switcher itself.
ACTIVE_CHARACTER_COOKIE = "active_character"

#: Only the recent activity feed shows on the dashboard, so nothing has a reason to read
#: further back than this in one request — and unlike positions, synced-trade rows are never
#: revisited once shown, so there is no correctness reason to load more.
ACTIVITY_WINDOW_DAYS = 90

#: An alert that has fired stays quiet for this long even while its condition keeps holding —
#: otherwise a price sitting past its target would refire on every single cron tick rather
#: than once, until the person watching it acts on it or the price moves back.
ALERT_COOLDOWN_HOURS = 12

#: Excludes 0/O and 1/I/L — characters easy to mistype or misread on a phone screen or a
#: scrap of paper, which is where a code with no Discord app or email behind it has to
#: survive. 32 characters ^ 16 positions is comfortably beyond brute-forcing regardless.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

#: Marks an account id that came from a recovery code rather than from Discord. The profile
#: page is the one place that has to tell the two apart — a Discord name is Discord's to
#: change, so only a code account is offered a rename it would actually keep.
_RECOVERY_ID_PREFIX = "local:"

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


def _market_breakdown(item: ItemMapping, point: MarketPoint, now: int) -> dict:
    """Every number the desktop window's item-details dialog shows, for one item.

    Assembled here rather than in the template so the page stays a list of already-decided
    values — the arithmetic that turns a raw market point into a margin belongs next to the
    ranking code that uses the same rules, not inside a Jinja expression.
    """
    buy_price, sell_price = offer_targets(point)
    tax = ge_tax(sell_price)
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    accounts = AccountsDB(settings.accounts_db_path)
    signer = SessionSigner(settings.session_secret)
    market = MarketCache(settings.data_dir / "market-cache")
    releases = ReleaseCache()
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    app = FastAPI(
        title="OSRS Toolkit",
        summary="Web front end for the OSRS Toolkit trade journal.",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Served from root, not /static, so its default scope covers the whole app.
        return Response(
            content=(STATIC_DIR / "sw.js").read_text(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.middleware("http")
    async def no_store_when_signed_in(request: Request, call_next):
        """Mark every response carrying account data as uncacheable.

        Every signed-in page here is account-specific, and nothing in the response previously
        said so — docs/deploying.md flags that as the reason a cache rule covering HTML on
        this domain could serve one account's dashboard to another. Cloudflare's default
        cache level does not cache HTML, so this changes nothing today; it is what makes
        turning caching on for the public pages a safe thing to do rather than a sharp edge.
        """
        response = await call_next(request)
        signed_in = COOKIE_NAME in request.cookies or request.headers.get("authorization")
        if signed_in:
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def desktop_token_account_id(authorization: str | None) -> str | None:
        """Whose account an ``Authorization: Bearer`` header names, if anyone's.

        The desktop window has no browser and so no session cookie. It presents a credential
        minted on the profile page instead, which resolves to exactly the same account the
        cookie would have -- every route below is then reachable by either, without each one
        having to know which arrived.
        """
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return accounts.account_id_for_desktop_token(token.strip())

    def optional_account(
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
        authorization: str | None = Header(default=None),
    ) -> Account | None:
        discord_user_id = signer.read(session) or desktop_token_account_id(authorization)
        return accounts.get(discord_user_id) if discord_user_id else None

    OptionalAccount = Depends(optional_account)

    def browser_account(
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> Account:
        """A signed-in browser, specifically -- a desktop credential will not do.

        Managing desktop credentials is deliberately out of reach of a desktop credential. If
        one leaks, what it can do is read and write the journal; it cannot mint a second one to
        outlive its own revocation, and it cannot sign the real desktop out.
        """
        discord_user_id = signer.read(session)
        account = accounts.get(discord_user_id) if discord_user_id else None
        if account is None:
            raise HTTPException(
                status_code=401, detail="Sign in from a browser to manage desktop access."
            )
        return account

    BrowserAccount = Depends(browser_account)

    def current_account(account: Account | None = OptionalAccount) -> Account:
        if account is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return account

    CurrentAccount = Depends(current_account)

    def active_character(
        active_character: str | None = Cookie(default=None, alias=ACTIVE_CHARACTER_COOKIE),
    ) -> str | None:
        return active_character or None

    ActiveCharacter = Depends(active_character)

    def character_switcher_context(account: Account, active: str | None) -> dict:
        """What every page's header needs to draw the character switcher, and what a route
        filtering its own journal reads by should use as the filter.

        Not validated against the sync service's own list of known characters: a hash it no
        longer recognises (the account was renamed, or the token was reset) still filters
        correctly on its own — nothing tagged with a hash nobody reports anymore simply reads
        as empty, the same as it would for any other character with nothing recorded yet —
        without spending a second network round trip on every request just to confirm a cookie.
        """
        return {
            "synced_characters": known_characters(settings, account),
            "active_character": active,
        }

    def _optional_switcher(account: Account | None, active: str | None) -> dict:
        """The same header context for a page that renders signed out as well as signed in.

        A signed-out visitor has no pairing token to ask the sync service about, so there is
        nothing to look up and no round trip worth spending on one.
        """
        if account is None:
            return {"synced_characters": [], "active_character": None}
        return character_switcher_context(account, active)

    # -- pages -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        if account is None:
            return templates.TemplateResponse(
                request,
                "landing.html",
                {"turnstile_site_key": settings.turnstile_site_key},
            )
        switcher = character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        repository = journal_for(settings, account)
        positions = repository.list_tracked(filter_hash)
        net_worth_history = repository.list_net_worth_history(filter_hash)
        # The live GE dashboard needs one specific character to ask the sync service about —
        # explicit when "All characters" isn't selected, or the only one there is when nobody
        # has had to choose yet. With several and no choice made, there is no single answer
        # worth showing, so the panel says so instead of guessing which one to display.
        synced = switcher["synced_characters"]
        live_hash = filter_hash or (synced[0]["account_hash"] if len(synced) == 1 else None)
        offer_slots, offer_screen = (
            live_offer_state(settings, account, live_hash) if live_hash else ({}, None)
        )
        offer_rows = [
            {**serialize(slot), "status_label": ge_offer_status_label(slot.state)}
            for slot in sorted(offer_slots.values(), key=lambda entry: entry.slot)
        ]
        net_worth_chart = net_worth_history_svg(
            [(point.captured_at, point.total_value) for point in net_worth_history]
        )
        # What the market currently suggests asking, per item, so a listing that the market has
        # since moved away from can be flagged the way the desktop window's "Needs attention"
        # card flags it. Missing prices simply flag nothing rather than blocking the page.
        try:
            _mappings, market_points = market.snapshot()
            live_sell_targets = {point.item_id: offer_targets(point)[1] for point in market_points}
        except MarketDataError:
            live_sell_targets = {}
        position_rows = []
        for position in positions:
            row = serialize(position)
            row["needs_attention"] = trade_needs_attention(
                position.status,
                position.asking_price,
                live_sell_targets.get(position.item_id),
            )
            position_rows.append(row)
        open_positions = [
            position for position in positions if position.status not in CLOSED_STATUSES
        ]
        total_invested = sum(position.invested for position in open_positions)
        desktop_state, desktop_last_seen = _desktop_activity(
            accounts.list_desktop_tokens(account.discord_user_id)
        )
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        net_worth_composition = _net_worth_composition(snapshot, total_invested)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "account": account,
                "positions": position_rows,
                "statuses": list(POSITION_STATUSES),
                "open_count": len(open_positions),
                "attention_count": sum(1 for row in position_rows if row["needs_attention"]),
                "total_invested": total_invested,
                "total_estimated_profit": sum(
                    position.estimated_profit for position in open_positions
                ),
                "net_worth": serialize(net_worth_history[-1]) if net_worth_history else None,
                "net_worth_chart": net_worth_chart,
                "net_worth_composition": net_worth_composition,
                "live_character_selected": live_hash is not None,
                "plugin_state": plugin_activity(settings, account),
                "desktop_state": desktop_state,
                "desktop_last_seen": desktop_last_seen,
                "offer_rows": offer_rows,
                "offer_screen": serialize(offer_screen) if offer_screen else None,
                **switcher,
            },
        )

    @app.get("/item/{item_id}", response_class=HTMLResponse)
    def item_history_page(
        request: Request,
        item_id: int,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        item_name = f"Item #{item_id}"
        breakdown = None
        try:
            mappings, points = market.snapshot()
            item = mappings.get(item_id)
            if item is not None:
                item_name = item.name
                point = next((entry for entry in points if entry.item_id == item_id), None)
                if point is not None:
                    breakdown = _market_breakdown(item, point, int(datetime.now(UTC).timestamp()))
        except MarketDataError:
            pass
        history = market.timeseries(item_id)
        watched = account is not None and item_id in accounts.list_watchlist_item_ids(
            account.discord_user_id
        )
        history_stats, volume_by_weekday = _history_stats(history)
        return templates.TemplateResponse(
            request,
            "item_history.html",
            {
                "account": account,
                "item_id": item_id,
                "item_name": item_name,
                "breakdown": breakdown,
                "watched": watched,
                "chart": price_history_svg(history),
                "volume_chart": volume_history_svg(history),
                "history_stats": history_stats,
                "volume_by_weekday": volume_by_weekday,
                "has_history": bool(history),
                **_optional_switcher(account, active),
            },
        )

    @app.get("/profile", response_class=HTMLResponse)
    def profile_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        return templates.TemplateResponse(
            request,
            "profile.html",
            {
                "account": account,
                "plugin_state": plugin_activity(settings, account),
                "is_recovery_account": account.discord_user_id.startswith(_RECOVERY_ID_PREFIX),
                "characters": [
                    serialize(character)
                    for character in accounts.list_characters(account.discord_user_id)
                ],
                "position_count": len(repository.list_tracked()),
                "trade_count": len(repository.list_all()),
                "watchlist_count": len(accounts.list_watchlist_item_ids(account.discord_user_id)),
                "desktop_tokens": [
                    {
                        "id": token.token_sha256,
                        "label": token.label,
                        "created_at": token.created_at,
                        "last_used_at": token.last_used_at,
                    }
                    for token in accounts.list_desktop_tokens(account.discord_user_id)
                ],
                **switcher,
            },
        )

    @app.get("/about", response_class=HTMLResponse)
    def about_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        """Readable signed out on purpose — what this is, who runs it, and what it does with
        someone's data are the questions asked *before* signing in, not after."""
        return templates.TemplateResponse(
            request, "about.html", {"account": account, **_optional_switcher(account, active)}
        )

    @app.get("/help", response_class=HTMLResponse)
    def help_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "help.html", {"account": account, **_optional_switcher(account, active)}
        )

    @app.get("/plugin", response_class=HTMLResponse)
    def plugin_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        """How to get the RuneLite plugin, which is now just "install it from the Plugin Hub".

        Signed out as well as in: somebody deciding whether this is worth an account is exactly
        who needs to read it.
        """
        return templates.TemplateResponse(
            request,
            "plugin.html",
            {
                "account": account,
                "plugin_repo_url": PLUGIN_REPO_URL,
                "plugin_source_url": PLUGIN_SOURCE_URL,
                "plugin_hub_name": PLUGIN_HUB_NAME,
                **_optional_switcher(account, active),
            },
        )

    @app.get("/download", response_class=HTMLResponse)
    def download_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        """The Windows app, offered from the site that replaced it.

        Signed out as well as in: somebody who has not made an account yet is exactly who is
        deciding between the two, and making them sign up first to find out the desktop app
        exists gets that backwards.
        """
        return templates.TemplateResponse(
            request,
            "download.html",
            {
                "account": account,
                "release": releases.latest(),
                "releases_url": RELEASES_URL,
                **_optional_switcher(account, active),
            },
        )

    @app.get("/api/export.csv")
    def export_csv(
        account: Account = CurrentAccount, active: str | None = ActiveCharacter
    ) -> Response:
        """A full backup of the Journal, not a view of whatever table or filter is on screen —
        manual trades have no character to filter by at all, and are always included."""
        filter_hash = character_switcher_context(account, active)["active_character"]
        repository = journal_for(settings, account)
        content = journal_csv(repository.list_tracked(filter_hash), repository.list_all())
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="runescope-journal.csv"'},
        )

    @app.get("/trades", response_class=HTMLResponse)
    def trades_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        # Manually entered trades only — synced activity has its own page and its own, real
        # account_hash to filter by. These are attributed by the separate character-naming
        # table instead (see accounts_db.py), which this switcher does not touch.
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        characters = accounts.list_characters(account.discord_user_id)
        names_by_id = {character.character_id: character.name for character in characters}
        trade_characters = accounts.get_trade_characters(account.discord_user_id)
        trades = []
        for trade in repository.list_all():
            row = serialize(trade)
            character_id = trade_characters.get(trade.sync_uid)
            row["character_name"] = names_by_id.get(character_id)
            trades.append(row)
        return templates.TemplateResponse(
            request,
            "trades.html",
            {
                "account": account,
                "trades": trades,
                "characters": [serialize(character) for character in characters],
                **switcher,
            },
        )

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        rows = []
        for event in repository.list_synced_trades(
            since=since, account_hash=switcher["active_character"]
        ):
            row = serialize(event)
            row["label"] = synced_trade_label(event)
            row["given_summary"] = compact_items(event.given)
            row["received_summary"] = compact_items(event.received)
            rows.append(row)
        return templates.TemplateResponse(
            request,
            "activity.html",
            {
                "account": account,
                "activity": rows,
                "window_days": ACTIVITY_WINDOW_DAYS,
                **switcher,
            },
        )

    @app.get("/counterparties", response_class=HTMLResponse)
    def counterparties_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        """Who player trades actually reach, and how each relationship has netted out —
        lifetime, not windowed like /activity: this is a relationship history, not a feed."""
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        trades = repository.list_synced_trades(
            "player_trade", account_hash=switcher["active_character"]
        )
        by_counterparty: dict[str, list] = {}
        for trade in trades:
            by_counterparty.setdefault(trade.counterparty or "Unknown", []).append(trade)
        rows = [
            {
                "counterparty": name,
                "trade_count": len(with_them),
                "given_value": sum(trade.given_value for trade in with_them),
                "received_value": sum(trade.received_value for trade in with_them),
                "net": sum(trade.estimated_difference for trade in with_them),
                "last_traded_at": max(trade.occurred_at for trade in with_them),
            }
            for name, with_them in by_counterparty.items()
        ]
        rows.sort(key=lambda row: row["last_traded_at"], reverse=True)
        return templates.TemplateResponse(
            request,
            "counterparties.html",
            {"account": account, "rows": rows, **switcher},
        )

    @app.get("/loot-log", response_class=HTMLResponse)
    def loot_log_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        events = repository.list_npc_loot_events(
            since=since, account_hash=switcher["active_character"]
        )
        rows = []
        by_npc: dict[str, list] = {}
        for event in events:
            row = serialize(event)
            row["items_summary"] = compact_items(event.items)
            rows.append(row)
            by_npc.setdefault(event.npc_name, []).append(event)
        rollup = [
            {
                "npc_name": npc_name,
                "deliveries": len(npc_events),
                "total_value": sum(npc_event.total_value for npc_event in npc_events),
                "observed_gp_per_hour": observed_gp_per_hour(npc_events),
            }
            for npc_name, npc_events in sorted(
                by_npc.items(),
                key=lambda pair: sum(npc_event.total_value for npc_event in pair[1]),
                reverse=True,
            )
        ]
        return templates.TemplateResponse(
            request,
            "loot_log.html",
            {
                "account": account,
                "loot": rows,
                "rollup": rollup,
                "window_days": ACTIVITY_WINDOW_DAYS,
                **switcher,
            },
        )

    @app.get("/death-log", response_class=HTMLResponse)
    def death_log_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        rows = []
        for event in repository.list_player_death_events(
            since=since, account_hash=switcher["active_character"]
        ):
            row = serialize(event)
            row["carried_summary"] = compact_items((*event.equipment, *event.inventory))
            rows.append(row)
        return templates.TemplateResponse(
            request,
            "death_log.html",
            {
                "account": account,
                "deaths": rows,
                "window_days": ACTIVITY_WINDOW_DAYS,
                **switcher,
            },
        )

    @app.get("/performance", response_class=HTMLResponse)
    def performance_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        period: str = "All time",
    ) -> HTMLResponse:
        if period not in PERIOD_FILTERS:
            period = "All time"
        switcher = character_switcher_context(account, active)
        repository = journal_for(settings, account)
        tracked = repository.list_tracked(switcher["active_character"])
        now = datetime.now(UTC)
        results = realized_results(tracked, repository.list_all(), period, now)
        summary = summarize(results)
        return templates.TemplateResponse(
            request,
            "performance.html",
            {
                "account": account,
                "period": period,
                "periods": list(PERIOD_FILTERS),
                "summary": serialize(summary),
                "by_strategy": [serialize(row) for row in by_strategy(results)],
                "by_item": [serialize(row) for row in by_item(results)],
                "calibration": [serialize(row) for row in calibration(tracked, period, now)],
                **switcher,
            },
        )

    @app.get("/flips", response_class=HTMLResponse)
    def flips_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        cash_stack: int | None = None,
        strategy: str = "Balanced (1–4h)",
        slots: int | None = None,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        repository = journal_for(settings, account)
        # None, not just falsy, is what "the query string left this out" means — a real
        # 0 typed into the cash field should stay 0, not fall back to a live reading. The
        # "prefilled" flags stay False when there was nothing live to prefill *from* —
        # they mark that the number came from sync, not merely that the field was blank.
        cash_stack_prefilled = False
        slots_prefilled = False
        if cash_stack is None or slots is None:
            snapshot = (
                repository.get_loadout_snapshot(filter_hash)
                if filter_hash
                else repository.get_latest_loadout_snapshot()
            )
            synced = switcher["synced_characters"]
            live_hash = filter_hash or (synced[0]["account_hash"] if len(synced) == 1 else None)
            if cash_stack is None:
                if snapshot is not None:
                    cash_stack = snapshot.quantity_owned(995)
                    cash_stack_prefilled = True
                else:
                    cash_stack = 10_000_000
            if slots is None:
                if live_hash:
                    offer_slots, _offer_screen = live_offer_state(settings, account, live_hash)
                    slots = 8 - len(offer_slots)
                    slots_prefilled = True
                else:
                    slots = 8
        # The Grand Exchange has exactly 8 slots, so anything outside that range describes a
        # game state that cannot exist — clamped rather than rejected, since a stray query
        # string should degrade to the nearest real answer instead of a 422 on a GET page.
        slots = max(1, min(8, slots))
        context = {
            "account": account,
            "strategies": list(STRATEGIES),
            "strategy": strategy,
            "cash_stack": cash_stack,
            "slots": slots,
            "cash_stack_prefilled": cash_stack_prefilled,
            "slots_prefilled": slots_prefilled,
            "watched_ids": set(accounts.list_watchlist_item_ids(account.discord_user_id)),
            **switcher,
        }
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            return templates.TemplateResponse(
                request, "flips.html", {**context, "market_unavailable": True}
            )
        candidates = rank_flips(
            mappings, points, cash_stack=cash_stack, strategy=strategy, slot_count=slots
        )
        portfolio = plan_flip_portfolio(candidates, cash_stack, slot_count=slots)
        return templates.TemplateResponse(
            request,
            "flips.html",
            {
                **context,
                "market_unavailable": False,
                "portfolio": [serialize(candidate) for candidate in portfolio],
                # Capped for the page rather than for the ranking itself — the portfolio above
                # already picked the offers actually worth using this cash stack on, and a
                # browsable list past a couple dozen rows stops being something anyone reads.
                "candidates": [serialize(candidate) for candidate in candidates[:30]],
            },
        )

    @app.get("/watchlist", response_class=HTMLResponse)
    def watchlist_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        context = {"account": account, **character_switcher_context(account, active)}
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            return templates.TemplateResponse(
                request, "watchlist.html", {**context, "market_unavailable": True}
            )
        point_by_id = {point.item_id: point for point in points}
        now = int(datetime.now(UTC).timestamp())
        alerts_by_item = {
            alert.item_id: alert
            for alert in accounts.list_watchlist_alerts(account.discord_user_id)
        }
        rows = []
        for item_id, alert in alerts_by_item.items():
            item, point = mappings.get(item_id), point_by_id.get(item_id)
            if item is None or point is None:
                # Delisted, or not yet in this snapshot — nothing priceable to show for it
                # right now, but the watch itself is untouched and reappears once it prices.
                continue
            buy_price, sell_price = offer_targets(point)
            profit = sell_price - buy_price - ge_tax(sell_price)
            rows.append(
                {
                    "item_id": item_id,
                    "name": item.name,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "profit": profit,
                    "roi": profit / buy_price * 100 if buy_price else 0.0,
                    "volume_1h": point.volume_1h,
                    "age_minutes": max(0, now - min(point.high_time, point.low_time)) // 60,
                    "target_price": alert.target_price,
                    "direction": alert.direction,
                }
            )
        rows.sort(key=lambda row: row["name"])
        return templates.TemplateResponse(
            request,
            "watchlist.html",
            {
                **context,
                "market_unavailable": False,
                "rows": rows,
                "webhook_configured": bool(account.discord_webhook_url),
            },
        )

    @app.post("/api/watchlist", status_code=201)
    def add_watchlist_item(payload: dict, account: Account = CurrentAccount) -> dict:
        """Watch an item, named either by the name someone typed or by an id a row already
        knows. The id path is what the watch toggles on the ranked tables use — they are
        looking at a row the market snapshot produced, so making them round-trip through a
        name match would only add a way for the exact item on screen to be misidentified."""
        item_id = payload.get("item_id")
        name = payload.get("name")
        if item_id is None and (not isinstance(name, str) or not name.strip()):
            raise HTTPException(status_code=422, detail="name is required")
        try:
            mappings, _points = market.snapshot()
        except MarketDataError:
            raise HTTPException(
                status_code=502, detail="Couldn't reach live prices, try again shortly."
            ) from None
        if item_id is not None:
            if not isinstance(item_id, int):
                raise HTTPException(status_code=422, detail="item_id must be a whole number")
            match = mappings.get(item_id)
            if match is None:
                raise HTTPException(status_code=404, detail="That item is not on the market.")
            added = accounts.add_watchlist_item(account.discord_user_id, match.item_id)
            if not added:
                raise HTTPException(
                    status_code=409, detail=f"{match.name} is already on your watchlist."
                )
            return {"ok": True, "item_id": match.item_id, "name": match.name}
        typed = name.strip().casefold()
        match = next((item for item in mappings.values() if item.name.casefold() == typed), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f'No item found named "{name.strip()}".')
        added = accounts.add_watchlist_item(account.discord_user_id, match.item_id)
        if not added:
            raise HTTPException(
                status_code=409, detail=f"{match.name} is already on your watchlist."
            )
        return {"ok": True, "item_id": match.item_id, "name": match.name}

    @app.delete("/api/watchlist/{item_id}")
    def remove_watchlist_item(item_id: int, account: Account = CurrentAccount) -> dict:
        accounts.remove_watchlist_item(account.discord_user_id, item_id)
        return {"ok": True}

    @app.put("/api/watchlist/{item_id}/alert")
    def set_watchlist_alert(item_id: int, payload: dict, account: Account = CurrentAccount) -> dict:
        target_price = payload.get("target_price")
        direction = payload.get("direction")
        if not isinstance(target_price, int) or isinstance(target_price, bool) or target_price <= 0:
            raise HTTPException(status_code=422, detail="target_price must be a positive number")
        if direction not in ("above", "below"):
            raise HTTPException(status_code=422, detail="direction must be 'above' or 'below'")
        if not account.discord_webhook_url:
            raise HTTPException(
                status_code=422, detail="Add a Discord webhook on your profile first."
            )
        # Watching first is idempotent (INSERT OR IGNORE) — arming an alert on an item not
        # already watched should watch it, not fail with "item not found".
        accounts.add_watchlist_item(account.discord_user_id, item_id)
        accounts.set_watchlist_alert(account.discord_user_id, item_id, target_price, direction)
        return {"ok": True}

    @app.delete("/api/watchlist/{item_id}/alert")
    def clear_watchlist_alert(item_id: int, account: Account = CurrentAccount) -> dict:
        accounts.clear_watchlist_alert(account.discord_user_id, item_id)
        return {"ok": True}

    @app.get("/alch", response_class=HTMLResponse)
    def alch_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
        cash_stack: int = 10_000_000,
        policy: str = "Safer",
        magic_level: int | None = None,
    ) -> HTMLResponse:
        if policy not in ALCH_POLICIES:
            policy = "Safer"
        watched_ids = (
            set(accounts.list_watchlist_item_ids(account.discord_user_id))
            if account is not None
            else set()
        )
        context = {
            "account": account,
            "cash_stack": cash_stack,
            "policy": policy,
            "policies": list(ALCH_POLICIES),
            "magic_level": magic_level,
            "watched_ids": watched_ids,
            **_optional_switcher(account, active),
        }
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            return templates.TemplateResponse(
                request, "alch.html", {**context, "market_unavailable": True}
            )
        candidates = alch_candidates(
            mappings, points, cash_stack=cash_stack, policy=policy, magic_level=magic_level
        )
        return templates.TemplateResponse(
            request,
            "alch.html",
            {
                **context,
                "market_unavailable": False,
                # Same cap and reasoning as the flip candidates list — a browsable table stops
                # being something anyone reads well past a couple dozen rows.
                "candidates": [serialize(candidate) for candidate in candidates[:40]],
            },
        )

    @app.get("/skilling", response_class=HTMLResponse)
    def skilling_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
        character: str = "",
    ) -> HTMLResponse:
        context = {
            "account": account,
            "character": character,
            **_optional_switcher(account, active),
        }
        skills: dict[str, int] = {}
        if character.strip():
            try:
                skills = fetch_player(character.strip()).skills
            except AccountLookupError as error:
                context["lookup_error"] = str(error)
        context["has_skills"] = bool(skills)
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            return templates.TemplateResponse(
                request, "skilling.html", {**context, "market_unavailable": True}
            )
        results = skill_results(mappings, points, skills)
        return templates.TemplateResponse(
            request,
            "skilling.html",
            {
                **context,
                "market_unavailable": False,
                "results": [serialize(result) for result in results],
            },
        )

    @app.get("/buy-limits", response_class=HTMLResponse)
    def buy_limits_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        try:
            mappings, _points = market.snapshot()
        except MarketDataError:
            return templates.TemplateResponse(
                request,
                "buy_limits.html",
                {"account": account, "market_unavailable": True, **switcher},
            )
        limits = {item.item_id: item.buy_limit for item in mappings.values() if item.buy_limit}
        repository = journal_for(settings, account)
        now = datetime.now(UTC)
        recent_fills = repository.list_synced_trades(
            event_type="ge_fill",
            since=now - timedelta(hours=4),
            account_hash=switcher["active_character"],
        )
        statuses = buy_limit_status(recent_fills, limits, now)
        return templates.TemplateResponse(
            request,
            "buy_limits.html",
            {
                "account": account,
                "market_unavailable": False,
                "statuses": [serialize(status) for status in statuses],
                **switcher,
            },
        )

    @app.get("/pvm", response_class=HTMLResponse)
    def pvm_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        repository = journal_for(settings, account)
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        readiness = assess_all(snapshot)
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            mappings, points = {}, []
        rows = []
        for entry in readiness:
            row = serialize(entry)
            if points:
                estimate = estimate_gp_per_hour(entry.activity, mappings, points)
                row["net_gp_per_hour"] = estimate.net_gp_per_hour if estimate.priced else None
            else:
                row["net_gp_per_hour"] = None
            rows.append(row)
        tracked = repository.list_tracked(filter_hash)
        return templates.TemplateResponse(
            request,
            "pvm.html",
            {
                "account": account,
                "readiness": rows,
                "has_snapshot": snapshot is not None,
                "supplies": [serialize(row) for row in supplies_spend_rows(tracked)],
                "total_supplies_spend": total_supplies_spend(tracked),
                **switcher,
            },
        )

    @app.get("/loadout", response_class=HTMLResponse)
    def loadout_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        repository = journal_for(settings, account)
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        skills_history = repository.list_skills_history(filter_hash)
        skills_chart = net_worth_history_svg(
            [(point.captured_at, point.total_level) for point in skills_history]
        )
        bank_rows: list[dict] = []
        bank_total_value = 0
        if snapshot is not None:
            bank_total_value = sum(item.total_value for item in snapshot.bank)
            bank_rows = sorted(
                (serialize(item) for item in snapshot.bank),
                key=lambda row: row["total_value"],
                reverse=True,
            )[:15]
        return templates.TemplateResponse(
            request,
            "loadout.html",
            {
                "account": account,
                "has_snapshot": snapshot is not None,
                "skills": dict(snapshot.skills) if snapshot is not None else {},
                "total_level": sum(snapshot.skills.values()) if snapshot is not None else 0,
                "skills_chart": skills_chart,
                "bank_rows": bank_rows,
                "bank_total_value": bank_total_value,
                "bank_item_count": len(snapshot.bank) if snapshot is not None else 0,
                **switcher,
            },
        )

    @app.get("/savings-goal", response_class=HTMLResponse)
    def savings_goal_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        # Deliberately not filtered by character: a savings goal is a target for the account as
        # a whole, and scoping it to whichever character happens to be selected would make the
        # same goal read as further away just for switching who the switcher is pointed at.
        context = {"account": account, **character_switcher_context(account, active)}
        if not account.has_goal:
            return templates.TemplateResponse(request, "savings_goal.html", context)
        repository = journal_for(settings, account)
        tracked = repository.list_tracked()
        manual = repository.list_all()
        since = datetime.fromisoformat(account.goal_created_at)
        now = datetime.now(UTC)
        saved = realized_profit_since(tracked, manual, since)
        progress = SavingsProgress(
            label=account.goal_label, target=account.goal_target, saved=saved
        )
        rate = daily_profit_rate(tracked, manual, now)
        eta_days = estimate_days_remaining(progress.remaining, rate)
        context["progress"] = serialize(progress)
        context["daily_rate"] = round(rate)
        context["eta_days"] = round(eta_days) if eta_days is not None else None
        return templates.TemplateResponse(request, "savings_goal.html", context)

    @app.post("/api/savings-goal")
    def set_savings_goal(payload: dict, account: Account = CurrentAccount) -> dict:
        label = payload.get("label")
        try:
            target = int(payload.get("target"))
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="target must be a whole number") from error
        if not isinstance(label, str) or not label.strip():
            raise HTTPException(status_code=422, detail="label is required")
        if target <= 0:
            raise HTTPException(status_code=422, detail="target must be a positive amount")
        accounts.set_savings_goal(account.discord_user_id, label.strip(), target)
        return {"ok": True}

    @app.delete("/api/savings-goal")
    def clear_savings_goal(account: Account = CurrentAccount) -> dict:
        accounts.clear_savings_goal(account.discord_user_id)
        return {"ok": True}

    # -- auth ----------------------------------------------------------------------------

    @app.get("/auth/discord/login")
    def discord_login() -> Response:
        if not settings.discord_client_id:
            raise HTTPException(
                status_code=503,
                detail="Discord sign-in is not configured on this server yet.",
            )
        state = secrets.token_urlsafe(24)
        url = discord_oauth.authorize_url(
            settings.discord_client_id, settings.discord_redirect_uri, state
        )
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            STATE_COOKIE, state, max_age=600, httponly=True, secure=True, samesite="lax"
        )
        return response

    @app.get("/auth/discord/callback")
    def discord_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
    ) -> Response:
        if not settings.discord_client_id or not settings.discord_client_secret:
            raise HTTPException(status_code=503, detail="Discord sign-in is not configured.")
        # Compared against our own cookie rather than trusted as-is: the state round-trips
        # through the browser, so an attacker who can plant their own callback URL in front of
        # a victim needs this check to fail before their session, not the victim's, gets used.
        if (
            not code
            or not state
            or not oauth_state
            or not secrets.compare_digest(state, oauth_state)
        ):
            raise HTTPException(status_code=400, detail="Invalid or expired sign-in attempt.")
        access_token = discord_oauth.exchange_code(
            settings.discord_client_id,
            settings.discord_client_secret,
            settings.discord_redirect_uri,
            code,
        )
        user = discord_oauth.fetch_user(access_token) if access_token else None
        if user is None:
            raise HTTPException(status_code=502, detail="Discord did not confirm the sign-in.")
        accounts.upsert_login(user.id, user.username, user.avatar_url)
        session = signer.issue(user.id)
        response = RedirectResponse("/", status_code=302)
        response.delete_cookie(STATE_COOKIE)
        response.set_cookie(
            COOKIE_NAME,
            session,
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    if settings.dev_login_enabled:
        # A real Discord application is the one piece of this that only the account holder can
        # create — client id, secret and redirect URI all live in Discord's own dashboard. This
        # signs a session in directly so the rest of the app can be built, tested and run before
        # that exists. TOOLKIT_DEV_LOGIN must never be set where the service is reachable by
        # anyone else: it hands out a session for whichever Discord id the caller names.
        @app.post("/auth/dev-login")
        def dev_login(payload: dict) -> Response:
            discord_user_id = payload.get("discord_user_id")
            username = payload.get("username", "Dev User")
            avatar = payload.get("avatar")
            if not isinstance(discord_user_id, str) or not discord_user_id:
                raise HTTPException(status_code=422, detail="discord_user_id is required")
            accounts.upsert_login(discord_user_id, username, avatar)
            response = JSONResponse({"ok": True})
            response.set_cookie(
                COOKIE_NAME,
                signer.issue(discord_user_id),
                max_age=MAX_AGE_SECONDS,
                httponly=True,
                secure=True,
                samesite="lax",
            )
            return response

    @app.post("/auth/recovery/create")
    def create_recovery_account(payload: dict | None = None) -> dict:
        """Sign in without Discord at all: a random code stands in for a whole identity.

        The code itself is never stored, the same way a sync pairing token never is — only
        its hash becomes the account's id, reusing ``get``/``upsert_login`` unchanged rather
        than adding a parallel login mechanism for this one path. It is returned exactly
        once, here, because there is nowhere on the server that could ever show it again: a
        lost code has no recovery of its own, which is the whole reason it works with no
        email address behind it.

        Which also makes this the cheapest account in the system to create — no Discord
        login, no mailbox, nothing to confirm — so it is the one door that gets a captcha.
        """
        payload = payload or {}
        if settings.turnstile_secret_key:
            token = payload.get("turnstile_token")
            verdict = turnstile.verify(
                settings.turnstile_secret_key, token if isinstance(token, str) else ""
            )
            if verdict is None:
                raise HTTPException(
                    status_code=503, detail="Couldn't check the box just now, try again."
                )
            if not verdict:
                raise HTTPException(
                    status_code=400, detail="Tick the box to show you are human, then try again."
                )
        display_name = payload.get("display_name")
        display_name = display_name.strip() if isinstance(display_name, str) else ""
        code = _generate_recovery_code()
        account_id = _recovery_account_id(code)
        accounts.upsert_login(account_id, display_name or "Player", None)
        response = JSONResponse({"ok": True, "code": code})
        response.set_cookie(
            COOKIE_NAME,
            signer.issue(account_id),
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @app.post("/auth/recovery/login")
    def recovery_login(payload: dict) -> Response:
        code = payload.get("code")
        if not isinstance(code, str) or not code.strip():
            raise HTTPException(status_code=422, detail="Enter your recovery code.")
        account = accounts.get(_recovery_account_id(code))
        if account is None:
            raise HTTPException(status_code=401, detail="That code was not recognized.")
        accounts.upsert_login(account.discord_user_id, account.username, account.avatar)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            signer.issue(account.discord_user_id),
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @app.post("/auth/logout")
    def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    # -- account ---------------------------------------------------------------------------

    @app.get("/api/me")
    def me(account: Account = CurrentAccount) -> dict:
        return {
            "discord_user_id": account.discord_user_id,
            "username": account.username,
            "avatar_url": account.avatar,
            "paired": account.paired,
        }

    @app.patch("/api/me")
    def rename_account(payload: dict, account: Account = CurrentAccount) -> dict:
        """Rename a recovery-code account.

        Refused for a Discord login rather than quietly allowed and then overwritten: Discord
        hands back a username on every sign-in, so a name set here would silently revert at
        the next one, which is worse than being told it is not yours to change.
        """
        if not account.discord_user_id.startswith(_RECOVERY_ID_PREFIX):
            raise HTTPException(
                status_code=409,
                detail="Your name comes from Discord, change it there and sign in again.",
            )
        username = payload.get("username")
        if not isinstance(username, str) or not username.strip():
            raise HTTPException(status_code=422, detail="Enter a display name.")
        accounts.set_display_name(account.discord_user_id, username.strip()[:40])
        return {"ok": True}

    @app.post("/api/account/delete")
    def delete_account(account: Account = BrowserAccount) -> Response:
        """Erase this account: journal, watchlist, characters, tokens, everything.

        Revokes the pairing token at the sync service first, so nothing the plugin already
        sent outlives this account waiting to be collected, then deletes the journal file and
        the account row -- which cascades characters, trade attributions, desktop tokens and
        watchlist items in one statement. Irreversible on purpose: the CSV export is the copy
        worth keeping if there is one, the same as it is for a departure that stops short of
        this.

        A browser session, not a desktop credential -- for the same reason minting and revoking
        those credentials is browser-only. A leaked desktop token may read and write the journal;
        it may not destroy it and the account with it, which is the one thing no re-pairing
        could undo.
        """
        if account.pairing_token:
            revoke_pairing_token(settings.sync_base_url, account.pairing_token)
        shutil.rmtree(settings.journal_dir(account.discord_user_id), ignore_errors=True)
        accounts.delete_account(account.discord_user_id)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.put("/api/pairing-token")
    def set_pairing_token(payload: dict, account: Account = BrowserAccount) -> dict:
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(status_code=422, detail="token is required")
        token = token.strip()
        valid = probe_token(settings.sync_base_url, token)
        if valid is False:
            raise HTTPException(status_code=422, detail="That pairing token was not accepted.")
        # A network hiccup (valid is None) still saves the token: a token that was correct a
        # moment ago is far more likely than one that just happened to be typed wrong at the
        # exact moment the service could not be reached, and the next read simply tries again.
        accounts.set_pairing_token(account.discord_user_id, token)
        return {"ok": True, "verified": valid is True}

    @app.delete("/api/pairing-token")
    def clear_pairing_token(account: Account = BrowserAccount) -> dict:
        accounts.set_pairing_token(account.discord_user_id, None)
        return {"ok": True}

    @app.put("/api/discord-webhook")
    def set_discord_webhook(payload: dict, account: Account = CurrentAccount) -> dict:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(status_code=422, detail="url is required")
        url = url.strip()
        if not is_discord_webhook_url(url):
            raise HTTPException(
                status_code=422,
                detail="That doesn't look like a Discord webhook URL "
                "(discord.com/api/webhooks/...).",
            )
        accounts.set_discord_webhook(account.discord_user_id, url)
        return {"ok": True}

    @app.delete("/api/discord-webhook")
    def clear_discord_webhook(account: Account = CurrentAccount) -> dict:
        accounts.set_discord_webhook(account.discord_user_id, None)
        return {"ok": True}

    @app.post("/api/pairing-token/generate")
    def generate_pairing_token(account: Account = BrowserAccount) -> dict:
        """A fresh token this account can paste straight into the plugin.

        Nothing else in the system ever mints one for a person — the plugin only accepts a
        token, and the sync service's own pairing endpoint is invite-gated while it is closed
        to the public. This is the one caller trusted to hold that invite code, so signing in
        here is what stands in for it: an account that reached this page already proved it,
        the same way it already proved enough to read its own journal.

        Browser-only, like the other three pairing routes and for the same reason as
        ``delete_account``: this hands back the plugin's *write* credential to the sync
        service, which is a strictly wider power than the journal access a desktop token
        already has. The desktop window never calls it — it only reads ``/api/sync-state``,
        ``/api/me`` and the three journal routes.
        """
        previous = account.pairing_token
        if previous:
            # Whatever the plugin has already sent but this app has not imported yet is about
            # to become unreachable: nothing will be able to authenticate for that queue once
            # the token is gone. Draining it first is the difference between replacing a
            # credential and losing the trades that were still in flight behind it.
            journal_for(settings, account)
        token = mint_pairing_token(settings.sync_base_url, settings.sync_invite_code)
        if token is None:
            raise HTTPException(
                status_code=502, detail="Couldn't reach the sync service, try again shortly."
            )
        accounts.set_pairing_token(account.discord_user_id, token)
        # Only once the replacement is safely minted and saved. Revoking first would leave an
        # account with no working token at all if the service went away in between.
        retired = bool(previous) and revoke_pairing_token(settings.sync_base_url, previous)
        return {"ok": True, "token": token, "previous_revoked": retired}

    # -- desktop access --------------------------------------------------------------------

    @app.get("/api/desktop-tokens")
    def list_desktop_tokens(account: Account = BrowserAccount) -> dict:
        return {
            "tokens": [
                {
                    "id": token.token_sha256,
                    "label": token.label,
                    "created_at": token.created_at,
                    "last_used_at": token.last_used_at,
                }
                for token in accounts.list_desktop_tokens(account.discord_user_id)
            ]
        }

    @app.post("/api/desktop-tokens", status_code=201)
    def create_desktop_token(payload: dict, account: Account = BrowserAccount) -> dict:
        """Mint a credential to paste into the desktop window.

        The token comes back exactly once. Nothing stores it in a form that could be shown
        again, so the response is the only chance to copy it -- which the profile page says
        plainly rather than letting somebody close the dialog and find out afterwards.
        """
        label = payload.get("label")
        label = label.strip()[:60] if isinstance(label, str) and label.strip() else "Desktop app"
        token = accounts.create_desktop_token(account.discord_user_id, label)
        return {"ok": True, "token": token, "label": label}

    @app.delete("/api/desktop-tokens/{token_id}")
    def revoke_desktop_token(token_id: str, account: Account = BrowserAccount) -> dict:
        if not accounts.revoke_desktop_token(account.discord_user_id, token_id):
            raise HTTPException(status_code=404, detail="No such desktop credential.")
        return {"ok": True}

    # -- journal mirror --------------------------------------------------------------------

    @app.get("/api/journal/version")
    def journal_version(account: Account = CurrentAccount) -> dict:
        """Has anything changed? Two indexed scalars per table and nothing serialized.

        The desktop polls this on a timer and pulls only when the answer moves.

        It imports from the sync service first, which this route deliberately did not do
        until 2026-08-30, on the reasoning that it had to stay the cheapest question here.
        That was wrong, and it broke the one data flow the whole product rests on: nothing
        else the desktop calls imports either, so a person using only the desktop window
        never ran an import at all. The plugin's events sat in the sync service until
        somebody happened to open the website in a browser -- 270 of them over three days,
        on the only real account, while the window sat there saying "RuneLite connected".
        A pull cannot fix it either: a pull only follows a version that moved, and without
        an import the version never moves.

        The cost that was being protected is one loopback call to a service on this same
        machine, once a minute per running desktop, and nothing to serialize when the queue
        is empty. An unreachable sync service yields no events rather than raising, so this
        still answers from the local journal when the service is down.
        """
        return journal_for(settings, account).sync_version()

    @app.get("/api/journal/pull")
    def journal_pull(account: Account = CurrentAccount, since: str | None = None) -> dict:
        """Rows changed after ``since``, or everything when it is omitted.

        Everything is what the first sync asks for, and on an account that has been running a
        while that is the whole journal in one response. It is a few hundred rows of small
        integers; paginating it would add a cursor to get wrong in exchange for nothing.
        """
        return stored_journal(settings, account).sync_export(since=since or None)

    @app.post("/api/journal/push")
    def journal_push(payload: dict, account: Account = CurrentAccount) -> dict:
        """Merge rows the desktop is holding, newest edit winning.

        Nothing here decides what a conflict means -- that lives in the journal itself, so
        that both sides of the mirror resolve one the same way rather than each having their
        own opinion about it.
        """
        applied = stored_journal(settings, account).sync_apply(payload)
        return {"ok": True, **applied}

    @app.get("/api/sync-state")
    def sync_state(account: Account = CurrentAccount, account_hash: str | None = None) -> dict:
        """Live plugin state, relayed to the desktop window.

        The payloads are passed through exactly as the sync service gave them rather than
        parsed into this app's own shapes. The desktop already has tested parsers for these,
        and relaying keeps there from being a second place where the meaning of a Grand
        Exchange slot is decided -- this route is a gate, not an interpreter.

        Takes the character explicitly rather than reading the browser's switcher cookie: the
        desktop has its own idea of which character is in front of the user and no cookie to
        put it in. With nothing named and exactly one synced character, that one is the only
        answer there is; with several, the caller has to say which.
        """
        # Everything the sync service lists is by definition a character it has seen play,
        # so there is nothing here to filter -- the list is already the synced ones.
        synced = known_characters(settings, account)
        target = account_hash or (synced[0]["account_hash"] if len(synced) == 1 else None)
        status, fresh, offers, screen = raw_sync_state(settings, account, target)
        return {
            "paired": account.paired,
            "status": status,
            "status_fresh": fresh,
            "account_hash": target,
            "offers": offers,
            "screen": screen,
            # Several characters and nothing chosen: the desktop has to ask which, rather than
            # be handed one of them and quietly show the wrong player's slots.
            "ambiguous": target is None and len(synced) > 1,
            "characters": synced,
        }

    @app.get("/api/plugin-state")
    def plugin_state_now(account: Account = CurrentAccount) -> dict:
        """Whether the plugin is sending, for a badge that has to stop lying as it sits.

        The badge is rendered server-side, so before this it was a snapshot of whenever the
        page happened to load and stayed that way until a reload -- someone watching it while
        the plugin reconnected saw "idle" indefinitely, next to a desktop app that had already
        noticed. ``plugin_activity`` caches for half a minute, so polling this is cheap and
        costs the sync service nothing beyond that.
        """
        return {"state": plugin_activity(settings, account)}

    @app.get("/api/accounts")
    def characters(account: Account = CurrentAccount) -> dict:
        """Every RuneLite character seen under this pairing, for a character switcher."""
        return {"accounts": known_characters(settings, account)}

    # -- journal ---------------------------------------------------------------------------

    @app.get("/api/positions")
    def positions(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = character_switcher_context(account, active)["active_character"]
        return {"positions": [serialize(trade) for trade in repository.list_tracked(filter_hash)]}

    @app.get("/api/trades")
    def trades(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        return {"trades": [serialize(trade) for trade in repository.list_all()]}

    @app.post("/api/trades", status_code=201)
    def add_trade(payload: dict, account: Account = CurrentAccount) -> dict:
        item_name = payload.get("item_name")
        try:
            quantity = int(payload.get("quantity"))
            buy_price = int(payload.get("buy_price"))
            sell_price = int(payload.get("sell_price"))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="quantity, buy_price and sell_price must all be whole numbers",
            ) from error
        if not isinstance(item_name, str) or not item_name.strip():
            raise HTTPException(status_code=422, detail="item_name is required")
        if quantity <= 0 or buy_price < 0 or sell_price < 0:
            raise HTTPException(
                status_code=422, detail="quantity must be positive, prices cannot be negative"
            )
        character_id = payload.get("character_id")
        if character_id is not None and not isinstance(character_id, int):
            raise HTTPException(status_code=422, detail="character_id must be a whole number")
        repository = journal_for(settings, account)
        trade_id = repository.add(item_name.strip(), quantity, buy_price, sell_price)
        sync_uid = repository.trade_sync_uid(trade_id)
        if character_id is not None and sync_uid:
            accounts.set_trade_character(account.discord_user_id, sync_uid, character_id)
        return {"ok": True, "trade_id": trade_id, "sync_uid": sync_uid}

    @app.delete("/api/trades/{trade_id}")
    def remove_trade(trade_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete(trade_id)
        return {"ok": True}

    @app.post("/api/positions", status_code=201)
    def track_position(
        payload: dict,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> dict:
        """Open a planned position, the way the desktop window's "Track this flip" does.

        Tagged with whichever character the switcher is pointed at, so a plan opened while
        looking at one account does not later read as belonging to every one of them. With
        "All characters" selected there is no answer to tag it with, and an untagged position
        is already treated as belonging to every character rather than to none.
        """
        item_name = payload.get("item_name")
        try:
            quantity = int(payload.get("quantity"))
            target_buy = int(payload.get("target_buy"))
            target_sell = int(payload.get("target_sell"))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="quantity, target_buy and target_sell must all be whole numbers",
            ) from error
        if not isinstance(item_name, str) or not item_name.strip():
            raise HTTPException(status_code=422, detail="item_name is required")
        if quantity <= 0 or target_buy < 0 or target_sell < 0:
            raise HTTPException(
                status_code=422, detail="quantity must be positive, prices cannot be negative"
            )
        item_id = payload.get("item_id")
        if item_id is not None and not isinstance(item_id, int):
            raise HTTPException(status_code=422, detail="item_id must be a whole number")
        strategy = payload.get("strategy")
        strategy = strategy if isinstance(strategy, str) and strategy in STRATEGIES else None
        repository = journal_for(settings, account)
        position_id = repository.track(
            item_id,
            item_name.strip(),
            quantity,
            target_buy,
            target_sell,
            account_hash=active or None,
            **({"strategy": strategy} if strategy else {}),
        )
        return {"ok": True, "position_id": position_id}

    @app.patch("/api/positions/{position_id}")
    def update_position(position_id: int, payload: dict, account: Account = CurrentAccount) -> dict:
        """Record what actually happened to a position — the desktop window's Update dialog.

        Fill lists are only rewritten when the caller sends them. Leaving one out keeps
        whatever the plugin has already recorded, which matters because a synced position's
        partial fills are history nobody typed and nobody should lose to a status change.
        """
        status = payload.get("status")
        if status not in POSITION_STATUSES:
            raise HTTPException(
                status_code=422, detail="That is not a status a position can be in."
            )
        quantity = _optional_int(payload.get("quantity"), "quantity")
        actual_buy = _optional_int(payload.get("actual_buy"), "actual_buy")
        actual_sell = _optional_int(payload.get("actual_sell"), "actual_sell")
        buy_fills = _fill_list(payload.get("buy_fills"), "buy_fills")
        sale_fills = _fill_list(payload.get("sale_fills"), "sale_fills")
        repository = journal_for(settings, account)
        try:
            repository.update_tracked(
                position_id,
                status,
                actual_buy,
                actual_sell,
                sale_fills=sale_fills,
                buy_fills=buy_fills,
                quantity=quantity,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True}

    @app.delete("/api/positions/{position_id}")
    def remove_position(position_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete_tracked(position_id)
        return {"ok": True}

    # -- market ------------------------------------------------------------------------------

    @app.get("/api/market")
    def market_status() -> dict:
        """How old the shared price snapshot is, for the header's price pill. No session
        needed: it says nothing about anyone's account, only about the wiki read every
        signed-in page already shares."""
        return {"age_seconds": market.age_seconds()}

    @app.post("/api/market/refresh")
    def refresh_market(account: Account = CurrentAccount) -> dict:
        """Fetch prices now instead of waiting out the refresh window.

        Signed-in only — not because the prices are private, but because an unauthenticated
        endpoint that makes this server call the wiki is a way to make it do that repeatedly.
        """
        market.invalidate()
        try:
            market.snapshot()
        except MarketDataError:
            raise HTTPException(
                status_code=502, detail="Couldn't reach live prices, try again shortly."
            ) from None
        return {"ok": True, "age_seconds": market.age_seconds()}

    @app.get("/api/characters")
    def characters_list(account: Account = CurrentAccount) -> dict:
        return {
            "characters": [
                serialize(character)
                for character in accounts.list_characters(account.discord_user_id)
            ]
        }

    @app.post("/api/characters", status_code=201)
    def add_character(payload: dict, account: Account = CurrentAccount) -> dict:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail="name is required")
        character = accounts.add_character(account.discord_user_id, name.strip())
        if character is None:
            raise HTTPException(
                status_code=409, detail="You already have a character with that name."
            )
        return {"ok": True, "character": serialize(character)}

    @app.delete("/api/characters/{character_id}")
    def remove_character(character_id: int, account: Account = CurrentAccount) -> dict:
        accounts.delete_character(account.discord_user_id, character_id)
        return {"ok": True}

    @app.get("/api/activity")
    def activity(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = character_switcher_context(account, active)["active_character"]
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        return {
            "activity": [
                serialize(event)
                for event in repository.list_synced_trades(since=since, account_hash=filter_hash)
            ]
        }

    @app.delete("/api/activity/{event_id}")
    def remove_activity(event_id: str, account: Account = CurrentAccount) -> dict:
        """Drop one imported event from the journal.

        For an event that should never have been counted — a trade with a friend that was
        really a gift, say. Imported events are acknowledged to the sync service as they land,
        so one deleted here is not handed back again by the next read.
        """
        journal_for(settings, account).delete_synced_trade(event_id)
        return {"ok": True}

    @app.delete("/api/loot-log/{event_id}")
    def remove_loot_log_event(event_id: str, account: Account = CurrentAccount) -> dict:
        journal_for(settings, account).delete_npc_loot_event(event_id)
        return {"ok": True}

    @app.delete("/api/death-log/{event_id}")
    def remove_death_log_event(event_id: str, account: Account = CurrentAccount) -> dict:
        journal_for(settings, account).delete_player_death_event(event_id)
        return {"ok": True}

    @app.get("/api/net-worth")
    def net_worth(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = character_switcher_context(account, active)["active_character"]
        return {
            "points": [serialize(point) for point in repository.list_net_worth_history(filter_hash)]
        }

    @app.get("/api/loadout")
    def loadout(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = character_switcher_context(account, active)["active_character"]
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        return {"loadout": serialize(snapshot) if snapshot else None}

    # -- internal: triggered by an external scheduler, not a process running inside this one --

    @app.post("/internal/check-alerts")
    def check_alerts(request: Request) -> dict:
        """Evaluate every armed price alert and fire the ones that now trigger.

        Deliberately a pull from outside rather than a scheduler living in this process — see
        ``config.Settings.internal_task_token``. A GitHub-style "run it on a timer yourself"
        endpoint: an OS-level scheduled task on the machine hosting this calls it every few
        minutes with the shared-secret header below. Unset token means the route does not
        exist as far as anyone without it can tell; wrong token means it exists but refuses.
        """
        if not settings.internal_task_token:
            raise HTTPException(status_code=404)
        provided = request.headers.get("X-Internal-Token")
        if not provided or not secrets.compare_digest(provided, settings.internal_task_token):
            raise HTTPException(status_code=401, detail="Invalid or missing internal token.")
        # Asked before the market is, so a scheduler tick with nothing armed costs one cheap
        # SQLite read rather than a full price refresh. The refresh is not free: this runs on
        # a timer longer than the market cache's own TTL, so every tick would otherwise find
        # the cache stale and pay for a real fetch to check nothing at all.
        armed = accounts.list_armed_alerts()
        if not armed:
            return {"ok": True, "checked": 0, "sent": 0}
        try:
            mappings, points = market.snapshot()
        except MarketDataError:
            return {"ok": True, "checked": 0, "sent": 0, "note": "market unavailable"}
        point_by_id = {point.item_id: point for point in points}
        now = datetime.now(UTC)
        checked = sent = 0
        for alert in armed:
            checked += 1
            if alert.last_alerted_at:
                last = datetime.fromisoformat(alert.last_alerted_at)
                if now - last < timedelta(hours=ALERT_COOLDOWN_HOURS):
                    continue
            item = mappings.get(alert.item_id)
            point = point_by_id.get(alert.item_id)
            if item is None or point is None:
                continue
            buy_price, sell_price = offer_targets(point)
            if alert.direction == "above":
                triggered, current_price = sell_price >= alert.target_price, sell_price
            else:
                triggered, current_price = buy_price <= alert.target_price, buy_price
            if not triggered:
                continue
            message = (
                f"**{item.name}** is now {alert.direction} your target of "
                f"{alert.target_price:,} gp, currently {current_price:,} gp. "
                f"https://runescope.app/item/{alert.item_id}"
            )
            if send_webhook_message(alert.discord_webhook_url, message):
                accounts.stamp_alert_sent(alert.discord_user_id, alert.item_id)
                sent += 1
        return {"ok": True, "checked": checked, "sent": sent}

    app.state.settings = settings
    app.state.accounts = accounts
    return app
