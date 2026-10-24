from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from osrs_toolkit.account import AccountLookupError, fetch_player
from osrs_toolkit.buy_limits import buy_limit_status
from osrs_toolkit.calculators import ALCH_POLICIES, alch_candidates, skill_results
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.ranking import (
    STRATEGIES,
    ge_tax,
    is_tax_exempt,
    offer_targets,
    plan_flip_portfolio,
    rank_flips,
)

from toolkit_web.accounts_db import Account
from toolkit_web.deps import Deps
from toolkit_web.importing import journal_for, live_offer_state
from toolkit_web.presentation import _buy_limit_room
from toolkit_web.serialize import serialize


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    market = deps.market
    templates = deps.templates
    OptionalAccount = deps.OptionalAccount
    CurrentAccount = deps.CurrentAccount
    ActiveCharacter = deps.ActiveCharacter

    @router.get("/flips", response_class=HTMLResponse)
    def flips_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        cash_stack: int | None = None,
        strategy: str = "Balanced (1–4h)",
        slots: int | None = None,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        repository = journal_for(settings, account)
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
        limit_room = _buy_limit_room(repository, mappings, filter_hash, datetime.now(UTC))
        candidates = rank_flips(
            mappings,
            points,
            cash_stack=cash_stack,
            strategy=strategy,
            slot_count=slots,
            remaining_limits=limit_room,
        )
        portfolio = plan_flip_portfolio(candidates, cash_stack, slot_count=slots)
        return templates.TemplateResponse(
            request,
            "flips.html",
            {
                **context,
                "market_unavailable": False,
                "limit_room": limit_room,
                "exempt_ids": {
                    candidate.item_id
                    for candidate in candidates
                    if is_tax_exempt(candidate.item_id, candidate.name)
                },
                "portfolio": [serialize(candidate) for candidate in portfolio],
                "candidates": [serialize(candidate) for candidate in candidates[:30]],
            },
        )

    @router.get("/watchlist", response_class=HTMLResponse)
    def watchlist_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        context = {"account": account, **deps.character_switcher_context(account, active)}
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
                continue
            buy_price, sell_price = offer_targets(point)
            profit = (
                sell_price
                - buy_price
                - ge_tax(sell_price, exempt=is_tax_exempt(item_id, item.name))
            )
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

    @router.post("/api/watchlist", status_code=201)
    def add_watchlist_item(payload: dict, account: Account = CurrentAccount) -> dict:
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

    @router.delete("/api/watchlist/{item_id}")
    def remove_watchlist_item(item_id: int, account: Account = CurrentAccount) -> dict:
        accounts.remove_watchlist_item(account.discord_user_id, item_id)
        return {"ok": True}

    @router.put("/api/watchlist/{item_id}/alert")
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
        accounts.add_watchlist_item(account.discord_user_id, item_id)
        accounts.set_watchlist_alert(account.discord_user_id, item_id, target_price, direction)
        return {"ok": True}

    @router.delete("/api/watchlist/{item_id}/alert")
    def clear_watchlist_alert(item_id: int, account: Account = CurrentAccount) -> dict:
        accounts.clear_watchlist_alert(account.discord_user_id, item_id)
        return {"ok": True}

    @router.get("/alch", response_class=HTMLResponse)
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
            **deps.optional_switcher(account, active),
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
                "candidates": [serialize(candidate) for candidate in candidates[:40]],
            },
        )

    @router.get("/skilling", response_class=HTMLResponse)
    def skilling_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
        character: str = "",
    ) -> HTMLResponse:
        context = {
            "account": account,
            "character": character,
            **deps.optional_switcher(account, active),
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

    @router.get("/buy-limits", response_class=HTMLResponse)
    def buy_limits_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/api/market")
    def market_status() -> dict:
        return {"age_seconds": market.age_seconds()}

    @router.post("/api/market/refresh")
    def refresh_market(account: Account = CurrentAccount) -> dict:
        market.invalidate()
        try:
            market.snapshot()
        except MarketDataError:
            raise HTTPException(
                status_code=502, detail="Couldn't reach live prices, try again shortly."
            ) from None
        return {"ok": True, "age_seconds": market.age_seconds()}

    return router
