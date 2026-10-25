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
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        account_hash: str | None = None,
    ) -> dict:
        character = account_hash or active
        repository = journal_for(settings, account)
        running = repository.active_trading_session(character)
        return {
            "active": serialize(running) if running else None,
            "mode": repository.flip_tracking_mode(),
            "recent": [
                serialize(entry) for entry in repository.list_trading_sessions(character, 10)
            ],
        }

    @router.post("/api/trading-session", status_code=201)
    def begin_trading_session(
        payload: dict | None = None,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        account_hash: str | None = None,
    ) -> dict:
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
        session = repository.start_trading_session(account_hash or active, label, started_at)
        return {"active": serialize(session)}

    @router.delete("/api/trading-session")
    def finish_trading_session(
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        account_hash: str | None = None,
    ) -> dict:
        repository = journal_for(settings, account)
        ended = repository.end_trading_session(account_hash or active)
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
        return journal_for(settings, account).sync_version()

    @router.get("/api/journal/pull")
    def journal_pull(account: Account = CurrentAccount, since: str | None = None) -> dict:
        return stored_journal(settings, account).sync_export(since=since or None)

    @router.post("/api/journal/push")
    def journal_push(payload: dict, account: Account = CurrentAccount) -> dict:
        applied = stored_journal(settings, account).sync_apply(payload)
        return {"ok": True, **applied}

    @router.get("/api/sync-state")
    def sync_state(account: Account = CurrentAccount, account_hash: str | None = None) -> dict:
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
            "ambiguous": target is None and len(synced) > 1,
            "characters": synced,
        }

    @router.get("/api/plugin-state")
    def plugin_state_now(account: Account = CurrentAccount) -> dict:
        return {"state": plugin_activity(settings, account)}

    @router.get("/api/accounts")
    def characters(account: Account = CurrentAccount) -> dict:
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

    @router.post("/api/positions/bulk")
    def bulk_positions(payload: dict, account: Account = CurrentAccount) -> dict:
        action = payload.get("action")
        raw_ids = payload.get("position_ids")
        if action not in ("supplies", "remove"):
            raise HTTPException(status_code=422, detail="action must be supplies or remove")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(entry, int) and not isinstance(entry, bool) for entry in raw_ids)
        ):
            raise HTTPException(
                status_code=422, detail="position_ids must be a list of whole numbers"
            )
        repository = journal_for(settings, account)
        owned = {trade.position_id for trade in repository.list_tracked()}
        wanted = [position_id for position_id in dict.fromkeys(raw_ids) if position_id in owned]
        if action == "supplies":
            result = repository.retag_positions(wanted, status="Supplies")
            return {"ok": True, "updated": result.updated}
        for position_id in wanted:
            repository.delete_tracked(position_id)
        return {"ok": True, "updated": len(wanted)}

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
