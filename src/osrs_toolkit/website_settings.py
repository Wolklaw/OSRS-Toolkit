from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass

from osrs_toolkit.web_source import ToolkitWebClient

SETTINGS_PATH = "/api/desktop-settings"
ALERT_DIRECTIONS = ("above", "below")


@dataclass(frozen=True, slots=True)
class WatchedItem:
    item_id: int
    target_price: int | None = None
    direction: str | None = None

    @property
    def has_alert(self) -> bool:
        return self.target_price is not None and self.direction in ALERT_DIRECTIONS


@dataclass(frozen=True, slots=True)
class SavingsGoal:
    label: str
    target: int
    created_at: str


@dataclass(frozen=True, slots=True)
class WebsiteSettings:
    watchlist: tuple[WatchedItem, ...]
    savings_goal: SavingsGoal | None
    webhook_configured: bool

    @property
    def watched_ids(self) -> set[int]:
        return {item.item_id for item in self.watchlist}

    def alert_for(self, item_id: int) -> WatchedItem | None:
        for item in self.watchlist:
            if item.item_id == item_id and item.has_alert:
                return item
        return None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_settings(payload: object) -> WebsiteSettings | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("watchlist"), list):
        return None
    watchlist = []
    for entry in payload["watchlist"]:
        if not isinstance(entry, dict) or _whole(entry.get("item_id")) is None:
            continue
        direction = entry.get("direction")
        watchlist.append(
            WatchedItem(
                item_id=entry["item_id"],
                target_price=_whole(entry.get("target_price")),
                direction=direction if direction in ALERT_DIRECTIONS else None,
            )
        )
    goal_payload = payload.get("savings_goal")
    goal = None
    if (
        isinstance(goal_payload, dict)
        and isinstance(goal_payload.get("label"), str)
        and _whole(goal_payload.get("target"))
    ):
        goal = SavingsGoal(
            label=goal_payload["label"],
            target=goal_payload["target"],
            created_at=str(goal_payload.get("created_at") or ""),
        )
    return WebsiteSettings(
        watchlist=tuple(watchlist),
        savings_goal=goal,
        webhook_configured=bool(payload.get("webhook_configured")),
    )


class WebsiteSettingsClient:
    def __init__(self, client: ToolkitWebClient) -> None:
        self.client = client

    @property
    def configured(self) -> bool:
        return self.client.configured

    @property
    def last_error(self) -> str:
        return self.client.last_error or "Could not reach the website"

    def fetch(self) -> WebsiteSettings | None:
        return parse_settings(self.client.get(SETTINGS_PATH))

    def watch(self, item_id: int) -> bool:
        if self.client.post("/api/watchlist", {"item_id": item_id}) is not None:
            return True
        return "already on your watchlist" in (self.client.last_error or "")

    def unwatch(self, item_id: int) -> bool:
        return self.client.delete(f"/api/watchlist/{item_id}") is not None

    def set_alert(self, item_id: int, target_price: int, direction: str) -> bool:
        body = {"target_price": target_price, "direction": direction}
        return self.client.put(f"/api/watchlist/{item_id}/alert", body) is not None

    def clear_alert(self, item_id: int) -> bool:
        return self.client.delete(f"/api/watchlist/{item_id}/alert") is not None

    def set_goal(self, label: str, target: int) -> bool:
        return self.client.post("/api/savings-goal", {"label": label, "target": target}) is not None

    def clear_goal(self) -> bool:
        return self.client.delete("/api/savings-goal") is not None

    def set_quests(self, account_hash: str, names: Iterable[str], completed: bool) -> bool:
        body = {"quest_names": sorted(names), "completed": completed, "account_hash": account_hash}
        return self.client.post("/api/quests", body) is not None

    def remove_event(self, kind: str, event_id: str) -> bool:
        path = {
            "activity": "/api/activity",
            "loot": "/api/loot-log",
            "death": "/api/death-log",
        }[kind]
        return self.client.delete(f"{path}/{urllib.parse.quote(event_id, safe='')}") is not None
