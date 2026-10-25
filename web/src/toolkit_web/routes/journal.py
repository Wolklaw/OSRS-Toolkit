from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from osrs_toolkit.cancellations import cancellation_groups, summarize_cancellations
from osrs_toolkit.csv_export import journal_csv
from osrs_toolkit.fill_times import fill_observations, fill_time_groups
from osrs_toolkit.formatting import compact_items, synced_trade_label
from osrs_toolkit.journal_presentation import PERIOD_FILTERS
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.performance import by_item, by_strategy, calibration, realized_results, summarize
from osrs_toolkit.pvm import assess_all, estimate_gp_per_hour, observed_gp_per_hour
from osrs_toolkit.savings_goal import (
    SavingsProgress,
    daily_profit_rate,
    estimate_days_remaining,
    realized_profit_since,
)
from osrs_toolkit.slippage import by_item_slippage, slippage_fills, summarize_slippage
from osrs_toolkit.supplies_report import supplies_spend_rows, total_supplies_spend

from toolkit_web.accounts_db import Account
from toolkit_web.charting import net_worth_history_svg
from toolkit_web.deps import Deps
from toolkit_web.importing import journal_for
from toolkit_web.routes import ACTIVITY_WINDOW_DAYS
from toolkit_web.serialize import serialize


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    market = deps.market
    templates = deps.templates
    CurrentAccount = deps.CurrentAccount
    ActiveCharacter = deps.ActiveCharacter

    @router.get("/api/export.csv")
    def export_csv(
        account: Account = CurrentAccount, active: str | None = ActiveCharacter
    ) -> Response:
        filter_hash = deps.character_switcher_context(account, active)["active_character"]
        repository = journal_for(settings, account)
        content = journal_csv(repository.list_tracked(filter_hash), repository.list_all())
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="runescope-journal.csv"'},
        )

    @router.get("/trades", response_class=HTMLResponse)
    def trades_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/counterparties", response_class=HTMLResponse)
    def counterparties_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/loot-log", response_class=HTMLResponse)
    def loot_log_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/death-log", response_class=HTMLResponse)
    def death_log_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/performance", response_class=HTMLResponse)
    def performance_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
        period: str = "All time",
    ) -> HTMLResponse:
        if period not in PERIOD_FILTERS:
            period = "All time"
        switcher = deps.character_switcher_context(account, active)
        repository = journal_for(settings, account)
        tracked = repository.list_tracked(switcher["active_character"])
        now = datetime.now(UTC)
        results = realized_results(tracked, repository.list_all(), period, now)
        summary = summarize(results)
        fills = slippage_fills(
            repository.list_synced_trades("ge_fill", account_hash=switcher["active_character"]),
            period,
            now,
        )
        offer_events = repository.list_offer_events(switcher["active_character"])
        cancel_groups = cancellation_groups(offer_events, period, now)
        fill_times = fill_time_groups(
            fill_observations(
                offer_events,
                repository.list_synced_trades("ge_fill", account_hash=switcher["active_character"]),
            ),
            period,
            now,
        )
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
                "fill_times": [serialize(row) for row in fill_times],
                "cancellations": [serialize(row) for row in cancel_groups],
                "cancellation_summary": serialize(summarize_cancellations(cancel_groups)),
                "slippage": serialize(summarize_slippage(fills)),
                "slippage_items": [serialize(row) for row in by_item_slippage(fills)],
                **switcher,
            },
        )

    @router.get("/pvm", response_class=HTMLResponse)
    def pvm_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/loadout", response_class=HTMLResponse)
    def loadout_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
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

    @router.get("/session", response_class=HTMLResponse)
    def session_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        switcher = deps.character_switcher_context(account, active)
        filter_hash = switcher["active_character"]
        synced = switcher["synced_characters"]
        live_hash = filter_hash or (synced[0]["account_hash"] if len(synced) == 1 else None)
        repository = journal_for(settings, account)
        summary = repository.session_summary(live_hash) if live_hash else None
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "account": account,
                "character_selected": live_hash is not None,
                "summary": serialize(summary) if summary is not None else None,
                **switcher,
            },
        )

    @router.get("/savings-goal", response_class=HTMLResponse)
    def savings_goal_page(
        request: Request,
        account: Account = CurrentAccount,
        active: str | None = ActiveCharacter,
    ) -> HTMLResponse:
        context = {"account": account, **deps.character_switcher_context(account, active)}
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

    @router.post("/api/savings-goal")
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

    @router.delete("/api/savings-goal")
    def clear_savings_goal(account: Account = CurrentAccount) -> dict:
        accounts.clear_savings_goal(account.discord_user_id)
        return {"ok": True}

    return router
