"""What a route needs that only ``create_app`` can build.

One object rather than a closure over six names, which is what let the routes move out of
``app.py`` and into modules of their own: a module-level route function cannot close over
anything. The ``Depends`` markers are built here rather than inside each router so that every
router shares one callable per dependency -- FastAPI caches a dependency's result per request
by the identity of the callable, so two routers each wrapping their own ``optional_account``
would resolve the same account twice in one request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException
from fastapi.templating import Jinja2Templates

from toolkit_web.accounts_db import Account, AccountsDB
from toolkit_web.config import Settings
from toolkit_web.importing import journal_for, known_characters
from toolkit_web.market_cache import MarketCache
from toolkit_web.releases import ReleaseCache
from toolkit_web.serialize import serialize
from toolkit_web.sessions import COOKIE_NAME, SessionSigner

#: Which synced character's data to show. Plain, not signed: it names nothing but an
#: account_hash the caller's own pairing token already has full read access to, so there is
#: nothing here worth protecting from the browser that couldn't already be read straight from
#: the API. "" means "every character" and is written back by the switcher itself.
ACTIVE_CHARACTER_COOKIE = "active_character"


@dataclass(frozen=True)
class Deps:
    """The services every router is handed, and the dependencies built from them."""

    settings: Settings
    accounts: AccountsDB
    signer: SessionSigner
    market: MarketCache
    releases: ReleaseCache
    templates: Jinja2Templates

    #: The resolver itself, for the one caller that has to invoke it by hand rather than let
    #: FastAPI resolve it: the error page, which runs outside any route's dependency tree.
    optional_account: Callable[[str | None, str | None], Account | None]

    #: ``Depends`` markers, capitalized the way a route's default argument reads them.
    OptionalAccount: Any
    BrowserAccount: Any
    CurrentAccount: Any
    ActiveCharacter: Any

    def character_switcher_context(self, account: Account, active: str | None) -> dict:
        """What every page's header needs to draw the character switcher, and what a route
        filtering its own journal reads by should use as the filter.

        Not validated against the sync service's own list of known characters: a hash it no
        longer recognises (the account was renamed, or the token was reset) still filters
        correctly on its own — nothing tagged with a hash nobody reports anymore simply reads
        as empty, the same as it would for any other character with nothing recorded yet —
        without spending a second network round trip on every request just to confirm a cookie.
        """
        return {
            "synced_characters": known_characters(self.settings, account),
            "active_character": active,
        }

    def optional_switcher(self, account: Account | None, active: str | None) -> dict:
        """The same header context for a page that renders signed out as well as signed in.

        A signed-out visitor has no pairing token to ask the sync service about, so there is
        nothing to look up and no round trip worth spending on one.
        """
        if account is None:
            return {"synced_characters": [], "active_character": None}
        return self.character_switcher_context(account, active)

    def trading_session_context(self, account: Account, filter_hash: str | None) -> dict:
        repository = journal_for(self.settings, account)
        running = repository.active_trading_session(filter_hash)
        return {
            "trading_session": serialize(running) if running else None,
            "flip_tracking_mode": repository.flip_tracking_mode(),
        }


def build(settings: Settings) -> Deps:
    accounts = AccountsDB(settings.accounts_db_path)
    signer = SessionSigner(settings.session_secret)

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

    def current_account(account: Account | None = OptionalAccount) -> Account:
        if account is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return account

    def active_character(
        active_character: str | None = Cookie(default=None, alias=ACTIVE_CHARACTER_COOKIE),
    ) -> str | None:
        return active_character or None

    return Deps(
        settings=settings,
        accounts=accounts,
        signer=signer,
        market=MarketCache(settings.data_dir / "market-cache"),
        releases=ReleaseCache(),
        templates=Jinja2Templates(directory=Path(__file__).parent / "templates"),
        optional_account=optional_account,
        OptionalAccount=OptionalAccount,
        BrowserAccount=Depends(browser_account),
        CurrentAccount=Depends(current_account),
        ActiveCharacter=Depends(active_character),
    )
