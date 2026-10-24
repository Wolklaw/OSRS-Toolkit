"""The JSON the desktop window and this app's own pages read.

Two readers with different needs share these: the browser's fetch() calls, and the desktop
mirror, which has no cookie and names the character it wants rather than reading a switcher.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from osrs_toolkit.ranking import STRATEGIES

from toolkit_web.accounts_db import Account
from toolkit_web.deps import Deps
from toolkit_web.importing import (
    journal_for,
    known_characters,
    plugin_activity,
    raw_sync_state,
    stored_journal,
)
from toolkit_web.presentation import _fill_list, _optional_int
from toolkit_web.routes import ACTIVITY_WINDOW_DAYS, POSITION_STATUSES
from toolkit_web.serialize import serialize


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    CurrentAccount = deps.CurrentAccount
    ActiveCharacter = deps.ActiveCharacter

    @router.get("/api/trading-session")
    def read_trading_session(
        account: Account = CurrentAccount, active: str | None = ActiveCharacter
    ) -> dict:
        repository = journal_for(settings, account)
        running = repository.active_trading_session(active)
        return {
            "active": serialize(running) if running else None,
            "mode": repository.flip_tracking_mode(),
            "recent": [serialize(entry) for entry in repository.list_trading_sessions(active, 10)],
        }

    @router.post("/api/trading-session", status_code=201)
    def begin_trading_session(
        payload: dict | None = None,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> dict:
        """Declare that Grand Exchange buys from here on are flips, not supplies.

        ``started_at`` backdates it, for the usual case of remembering the button an hour
        in; it is clamped to a day so a stray value cannot retag a whole journal.
        """
        payload = payload or {}
        label = str(payload.get("label") or "").strip()[:80]
        started_at = None
        raw_minutes = payload.get("backdate_minutes")
        if raw_minutes is not None:
            try:
                minutes = int(raw_minutes)
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=422, detail="backdate_minutes must be a whole number"
                ) from error
            minutes = max(0, min(minutes, 24 * 60))
            started_at = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        repository = journal_for(settings, account)
        session = repository.start_trading_session(active, label, started_at)
        return {"active": serialize(session)}

    @router.delete("/api/trading-session")
    def finish_trading_session(
        account: Account = CurrentAccount, active: str | None = ActiveCharacter
    ) -> dict:
        repository = journal_for(settings, account)
        ended = repository.end_trading_session(active)
        return {"ended": serialize(ended) if ended else None}

    @router.put("/api/flip-tracking-mode")
    def set_flip_tracking_mode(payload: dict, account: Account = CurrentAccount) -> dict:
        mode = str(payload.get("mode") or "")
        repository = journal_for(settings, account)
        try:
            repository.set_flip_tracking_mode(mode)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"mode": mode}

    @router.get("/api/journal/version")
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

    @router.get("/api/journal/pull")
    def journal_pull(account: Account = CurrentAccount, since: str | None = None) -> dict:
        """Rows changed after ``since``, or everything when it is omitted.

        Everything is what the first sync asks for, and on an account that has been running a
        while that is the whole journal in one response. It is a few hundred rows of small
        integers; paginating it would add a cursor to get wrong in exchange for nothing.
        """
        return stored_journal(settings, account).sync_export(since=since or None)

    @router.post("/api/journal/push")
    def journal_push(payload: dict, account: Account = CurrentAccount) -> dict:
        """Merge rows the desktop is holding, newest edit winning.

        Nothing here decides what a conflict means -- that lives in the journal itself, so
        that both sides of the mirror resolve one the same way rather than each having their
        own opinion about it.
        """
        applied = stored_journal(settings, account).sync_apply(payload)
        return {"ok": True, **applied}

    @router.get("/api/sync-state")
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

    @router.get("/api/plugin-state")
    def plugin_state_now(account: Account = CurrentAccount) -> dict:
        """Whether the plugin is sending, for a badge that has to stop lying as it sits.

        The badge is rendered server-side, so before this it was a snapshot of whenever the
        page happened to load and stayed that way until a reload -- someone watching it while
        the plugin reconnected saw "idle" indefinitely, next to a desktop app that had already
        noticed. ``plugin_activity`` caches for half a minute, so polling this is cheap and
        costs the sync service nothing beyond that.
        """
        return {"state": plugin_activity(settings, account)}

    @router.get("/api/accounts")
    def characters(account: Account = CurrentAccount) -> dict:
        """Every RuneLite character seen under this pairing, for a character switcher."""
        return {"accounts": known_characters(settings, account)}

    @router.get("/api/positions")
    def positions(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        return {"positions": [serialize(trade) for trade in repository.list_tracked(filter_hash)]}

    @router.get("/api/trades")
    def trades(account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        return {"trades": [serialize(trade) for trade in repository.list_all()]}

    @router.post("/api/trades", status_code=201)
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

    @router.delete("/api/trades/{trade_id}")
    def remove_trade(trade_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete(trade_id)
        return {"ok": True}

    @router.post("/api/positions", status_code=201)
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

    @router.patch("/api/positions/{position_id}")
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

    @router.delete("/api/positions/{position_id}")
    def remove_position(position_id: int, account: Account = CurrentAccount) -> dict:
        repository = journal_for(settings, account)
        repository.delete_tracked(position_id)
        return {"ok": True}

    @router.get("/api/characters")
    def characters_list(account: Account = CurrentAccount) -> dict:
        return {
            "characters": [
                serialize(character)
                for character in accounts.list_characters(account.discord_user_id)
            ]
        }

    @router.post("/api/characters", status_code=201)
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

    @router.delete("/api/characters/{character_id}")
    def remove_character(character_id: int, account: Account = CurrentAccount) -> dict:
        accounts.delete_character(account.discord_user_id, character_id)
        return {"ok": True}

    @router.get("/api/activity")
    def activity(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        since = datetime.now(UTC) - timedelta(days=ACTIVITY_WINDOW_DAYS)
        return {
            "activity": [
                serialize(event)
                for event in repository.list_synced_trades(since=since, account_hash=filter_hash)
            ]
        }

    @router.delete("/api/activity/{event_id}")
    def remove_activity(event_id: str, account: Account = CurrentAccount) -> dict:
        """Drop one imported event from the journal.

        For an event that should never have been counted — a trade with a friend that was
        really a gift, say. Imported events are acknowledged to the sync service as they land,
        so one deleted here is not handed back again by the next read.
        """
        journal_for(settings, account).delete_synced_trade(event_id)
        return {"ok": True}

    @router.delete("/api/loot-log/{event_id}")
    def remove_loot_log_event(event_id: str, account: Account = CurrentAccount) -> dict:
        journal_for(settings, account).delete_npc_loot_event(event_id)
        return {"ok": True}

    @router.delete("/api/death-log/{event_id}")
    def remove_death_log_event(event_id: str, account: Account = CurrentAccount) -> dict:
        journal_for(settings, account).delete_player_death_event(event_id)
        return {"ok": True}

    @router.get("/api/net-worth")
    def net_worth(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        return {
            "points": [serialize(point) for point in repository.list_net_worth_history(filter_hash)]
        }

    @router.get("/api/loadout")
    def loadout(account: Account = CurrentAccount, active: str | None = ActiveCharacter) -> dict:
        repository = journal_for(settings, account)
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        snapshot = (
            repository.get_loadout_snapshot(filter_hash)
            if filter_hash
            else repository.get_latest_loadout_snapshot()
        )
        return {"loadout": serialize(snapshot) if snapshot else None}

    return router
