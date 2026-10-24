"""The dashboard, one item's history, and the pages readable without an account."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from osrs_toolkit.journal_presentation import trade_needs_attention
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.ranking import offer_targets
from osrs_toolkit.runelite_sync import ge_offer_status_label

from toolkit_web.accounts_db import Account
from toolkit_web.charting import net_worth_history_svg, price_history_svg, volume_history_svg
from toolkit_web.deps import Deps
from toolkit_web.importing import journal_for, live_offer_state, plugin_activity
from toolkit_web.presentation import (
    _desktop_activity,
    _history_stats,
    _market_breakdown,
    _net_worth_composition,
)
from toolkit_web.releases import RELEASES_URL
from toolkit_web.routes import CLOSED_STATUSES, POSITION_STATUSES
from toolkit_web.serialize import serialize

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


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    market = deps.market
    releases = deps.releases
    templates = deps.templates
    OptionalAccount = deps.OptionalAccount
    ActiveCharacter = deps.ActiveCharacter

    @router.get("/", response_class=HTMLResponse)
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
        switcher = deps.character_switcher_context(account, active)
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
        session_summary = repository.session_summary(live_hash) if live_hash else None
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
                "session_summary": serialize(session_summary) if session_summary else None,
                "live_character_selected": live_hash is not None,
                "plugin_state": plugin_activity(settings, account),
                "desktop_state": desktop_state,
                "desktop_last_seen": desktop_last_seen,
                "offer_rows": offer_rows,
                "offer_screen": serialize(offer_screen) if offer_screen else None,
                **deps.trading_session_context(account, filter_hash),
                **switcher,
            },
        )

    @router.get("/item/{item_id}", response_class=HTMLResponse)
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
                **deps.optional_switcher(account, active),
            },
        )

    @router.get("/about", response_class=HTMLResponse)
    def about_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        """Readable signed out on purpose — what this is, who runs it, and what it does with
        someone's data are the questions asked *before* signing in, not after."""
        return templates.TemplateResponse(
            request, "about.html", {"account": account, **deps.optional_switcher(account, active)}
        )

    @router.get("/help", response_class=HTMLResponse)
    def help_page(
        request: Request,
        account: Account | None = OptionalAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "help.html", {"account": account, **deps.optional_switcher(account, active)}
        )

    @router.get("/plugin", response_class=HTMLResponse)
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
                **deps.optional_switcher(account, active),
            },
        )

    @router.get("/download", response_class=HTMLResponse)
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
                **deps.optional_switcher(account, active),
            },
        )

    return router
