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

ACTIVE_CHARACTER_COOKIE = "active_character"


@dataclass(frozen=True)
class Deps:
    settings: Settings
    accounts: AccountsDB
    signer: SessionSigner
    market: MarketCache
    releases: ReleaseCache
    templates: Jinja2Templates

    optional_account: Callable[[str | None, str | None], Account | None]

    OptionalAccount: Any
    BrowserAccount: Any
    CurrentAccount: Any
    ActiveCharacter: Any

    def character_switcher_context(self, account: Account, active: str | None) -> dict:
        return {
            "synced_characters": known_characters(self.settings, account),
            "active_character": active,
        }

    def optional_switcher(self, account: Account | None, active: str | None) -> dict:
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
