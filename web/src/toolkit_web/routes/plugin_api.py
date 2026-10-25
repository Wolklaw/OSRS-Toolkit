from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException
from osrs_toolkit.ge_suggestions import (
    GeSuggestion,
    combine,
    position_suggestions,
    watchlist_suggestions,
)
from osrs_toolkit.market import MarketDataError

from toolkit_web.accounts_db import Account
from toolkit_web.deps import Deps
from toolkit_web.importing import journal_for
from toolkit_web.presentation import _buy_limit_room
from toolkit_web.serialize import serialize

SUGGESTION_CACHE_SECONDS = 10.0
COINS_ITEM_ID = 995


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    market = deps.market
    cache: dict[tuple[str, str], tuple[float, list[GeSuggestion]]] = {}
    lock = threading.Lock()

    def paired_account(authorization: str | None) -> Account:
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                account = accounts.account_for_pairing_token(token.strip())
                if account is not None:
                    return account
        raise HTTPException(status_code=401, detail="Pairing token required")

    def compute(account: Account, account_hash: str) -> list[GeSuggestion]:
        repository = journal_for(settings, account)
        from_positions = position_suggestions(repository.list_tracked(account_hash))
        watched = accounts.list_watchlist_item_ids(account.discord_user_id)
        from_watchlist: list[GeSuggestion] = []
        if watched:
            try:
                mappings, points = market.snapshot()
            except MarketDataError:
                return from_positions
            snapshot = repository.get_loadout_snapshot(account_hash)
            owned = (
                {item_id: snapshot.quantity_owned(item_id) for item_id in watched}
                if snapshot is not None
                else {}
            )
            from_watchlist = watchlist_suggestions(
                watched,
                mappings,
                {point.item_id: point for point in points},
                owned,
                _buy_limit_room(repository, mappings, account_hash, datetime.now(UTC)),
            )
        return combine(from_positions, from_watchlist)

    @router.get("/api/plugin/ge-suggestions")
    def ge_suggestions(account_hash: str, authorization: str | None = Header(default=None)) -> dict:
        account = paired_account(authorization)
        key = (account.discord_user_id, account_hash)
        now = time.monotonic()
        with lock:
            cached = cache.get(key)
            if cached is not None and now - cached[0] < SUGGESTION_CACHE_SECONDS:
                return {"suggestions": [serialize(item) for item in cached[1]]}
        suggestions = compute(account, account_hash)
        with lock:
            for stale, (stamp, _value) in list(cache.items()):
                if now - stamp >= SUGGESTION_CACHE_SECONDS:
                    del cache[stale]
            cache[key] = (now, suggestions)
        return {"suggestions": [serialize(item) for item in suggestions]}

    return router
