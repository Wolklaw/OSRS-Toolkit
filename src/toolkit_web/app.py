"""HTTP surface.

Sign-in is Discord OAuth and nothing else — no password, no email address held anywhere. A
signed session cookie carries a Discord user id; everything past that reads or writes that
user's own journal, importing from the sync service first so a page is never more than one
request stale. See ``toolkit_web.importing`` for why that is a request-time step rather than a
background worker.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from osrs_toolkit.formatting import compact_items, synced_trade_label
from osrs_toolkit.sync_source import HttpSyncSource

from toolkit_web import discord_oauth
from toolkit_web.accounts_db import Account, AccountsDB
from toolkit_web.config import Settings
from toolkit_web.importing import journal_for
from toolkit_web.serialize import serialize
from toolkit_web.sessions import COOKIE_NAME, MAX_AGE_SECONDS, SessionSigner
from toolkit_web.token_probe import probe_token

STATE_COOKIE = "oauth_state"

#: Only the recent activity feed shows on the dashboard, so nothing has a reason to read
#: further back than this in one request — and unlike positions, synced-trade rows are never
#: revisited once shown, so there is no correctness reason to load more.
ACTIVITY_WINDOW_DAYS = 90


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    accounts = AccountsDB(settings.accounts_db_path)
    signer = SessionSigner(settings.session_secret)
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    app = FastAPI(
        title="OSRS Toolkit",
        summary="Web front end for the OSRS Toolkit trade journal.",
    )

    def optional_account(
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> Account | None:
        discord_user_id = signer.read(session)
        return accounts.get(discord_user_id) if discord_user_id else None

    OptionalAccount = Depends(optional_account)

    def current_account(account: Account | None = OptionalAccount) -> Account:
        if account is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return account

    CurrentAccount = Depends(current_account)

    # -- pages -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, account: Account | None = OptionalAccount) -> HTMLResponse:
        if account is None:
            return templates.TemplateResponse(request, "landing.html")
        repository = journal_for(settings, account)
        positions = repository.list_tracked()
        net_worth_history = repository.list_net_worth_history()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "account": account,
                "positions": [serialize(position) for position in positions],
                "total_invested": sum(position.invested for position in positions),
                "total_estimated_profit": sum(position.estimated_profit for position in positions),
                "net_worth": serialize(net_worth_history[-1]) if net_worth_history else None,
            },
        )

    @app.get("/trades", response_class=HTMLResponse)
    def trades_page(request: Request, account: Account = CurrentAccount) -> HTMLResponse:
        repository = journal_for(settings, account)
        return templates.TemplateResponse(
            request,
            "trades.html",
            {
                "account": account,
                "trades": [serialize(trade) for trade in repository.list_all()],
            },
        )

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, account: Account = CurrentAccount) -> HTMLResponse:
        repository = journal_for(settings, account)
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        rows = []
        for event in repository.list_synced_trades(since=since):
            row = serialize(event)
            row["label"] = synced_trade_label(event)
            row["given_summary"] = compact_items(event.given)
            row["received_summary"] = compact_items(event.received)
            rows.append(row)
        return templates.TemplateResponse(
            request,
            "activity.html",
            {"account": account, "activity": rows, "window_days": ACTIVITY_WINDOW_DAYS},
        )

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
            if not isinstance(discord_user_id, str) or not discord_user_id:
                raise HTTPException(status_code=422, detail="discord_user_id is required")
            accounts.upsert_login(discord_user_id, username, None)
            response = JSONResponse({"ok": True})
            response.set_cookie(
                COOKIE_NAME,
                signer.issue(discord_user_id),
                max_age=MAX_AGE_SECONDS,
                httponly=True,
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

    @app.put("/api/pairing-token")
    def set_pairing_token(payload: dict, account: Account = CurrentAccount) -> dict:
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
    def clear_pairing_token(account: Account = CurrentAccount) -> dict:
        accounts.set_pairing_token(account.discord_user_id, None)
        return {"ok": True}

    @app.get("/api/accounts")
    def characters(account: Account = CurrentAccount) -> dict:
        """Every RuneLite character seen under this pairing, for a character switcher."""
        if not account.pairing_token:
            return {"accounts": []}
        source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
        return {"accounts": source.known_accounts()}

    # -- journal ---------------------------------------------------------------------------

    @app.get("/api/positions")
    def positions(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        return {"positions": [serialize(trade) for trade in repository.list_tracked()]}

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
        repository = journal_for(settings, account)
        trade_id = repository.add(item_name.strip(), quantity, buy_price, sell_price)
        return {"ok": True, "trade_id": trade_id}

    @app.delete("/api/trades/{trade_id}")
    def remove_trade(trade_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete(trade_id)
        return {"ok": True}

    @app.delete("/api/positions/{position_id}")
    def remove_position(position_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete_tracked(position_id)
        return {"ok": True}

    @app.get("/api/activity")
    def activity(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        return {
            "activity": [serialize(event) for event in repository.list_synced_trades(since=since)]
        }

    @app.get("/api/net-worth")
    def net_worth(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        return {"points": [serialize(point) for point in repository.list_net_worth_history()]}

    @app.get("/api/loadout")
    def loadout(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        snapshot = repository.get_latest_loadout_snapshot()
        return {"loadout": serialize(snapshot) if snapshot else None}

    app.state.settings = settings
    app.state.accounts = accounts
    return app
