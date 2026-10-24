from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from osrs_toolkit.market import MarketDataError
from osrs_toolkit.ranking import offer_targets

from toolkit_web.deps import Deps
from toolkit_web.discord_webhook import send_webhook_message

ALERT_COOLDOWN_HOURS = 12


def build(deps: Deps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    accounts = deps.accounts
    market = deps.market

    @router.post("/internal/check-alerts")
    def check_alerts(request: Request) -> dict:
        if not settings.internal_task_token:
            raise HTTPException(status_code=404)
        provided = request.headers.get("X-Internal-Token")
        if not provided or not secrets.compare_digest(provided, settings.internal_task_token):
            raise HTTPException(status_code=401, detail="Invalid or missing internal token.")
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

    return router
