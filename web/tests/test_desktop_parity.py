from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from osrs_toolkit.journal import JournalRepository, LoadoutItem, LoadoutSnapshot, NpcLootRecord

from toolkit_web.config import Settings

_WEBHOOK = "https://discord.com/api/webhooks/123/abc"


def _desktop(signed_in: TestClient) -> tuple[TestClient, dict[str, str]]:
    token = signed_in.post("/api/desktop-tokens", json={"label": "Desktop"}).json()["token"]
    bare = TestClient(signed_in.app, base_url="https://testserver")
    return bare, {"Authorization": f"Bearer {token}"}


def _seed(settings: Settings) -> JournalRepository:
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(),
            bank=(LoadoutItem(995, "Coins", 5_000, 1),),
            skills={"Attack": 60},
        )
    )
    repository.add_npc_loot_event(
        NpcLootRecord(
            event_id="kill-1",
            occurred_at=datetime.now(UTC).isoformat(),
            account_hash="abc123",
            account_name="Wolklaw",
            npc_name="Vorkath",
            items=(LoadoutItem(536, "Dragon bones", 2, 2_000),),
        )
    )
    return repository


def test_the_desktop_pulls_plugin_data_with_its_token(
    signed_in: TestClient, settings: Settings
) -> None:
    _seed(settings)
    bare, headers = _desktop(signed_in)

    payload = bare.post("/api/journal/plugin-data", json={}, headers=headers).json()

    assert [row["event_id"] for row in payload["rows"]["npc_loot_events"]] == ["kill-1"]
    assert payload["rows"]["npc_loot_events"][0]["items"][0]["item_name"] == "Dragon bones"
    assert len(payload["rows"]["loadout_snapshot_history"]) == 1
    assert payload["cursors"]["npc_loot_events"] >= 1


def test_a_second_pull_from_its_cursors_carries_nothing_new(
    signed_in: TestClient, settings: Settings
) -> None:
    _seed(settings)
    bare, headers = _desktop(signed_in)
    first = bare.post("/api/journal/plugin-data", json={}, headers=headers).json()

    second = bare.post(
        "/api/journal/plugin-data", json={"cursors": first["cursors"]}, headers=headers
    ).json()

    assert second["rows"]["loadout_snapshot_history"] == []
    assert second["more"] is False


def test_plugin_data_needs_a_session(client: TestClient) -> None:
    assert client.post("/api/journal/plugin-data", json={}).status_code == 401


def test_desktop_settings_start_empty(signed_in: TestClient) -> None:
    bare, headers = _desktop(signed_in)

    assert bare.get("/api/desktop-settings", headers=headers).json() == {
        "watchlist": [],
        "savings_goal": None,
        "webhook_configured": False,
    }


def test_desktop_settings_carry_the_goal_and_webhook_state(signed_in: TestClient) -> None:
    signed_in.post("/api/savings-goal", json={"label": "Twisted bow", "target": 1_000_000_000})
    signed_in.put("/api/discord-webhook", json={"url": _WEBHOOK})
    bare, headers = _desktop(signed_in)

    answer = bare.get("/api/desktop-settings", headers=headers).json()

    assert answer["savings_goal"]["label"] == "Twisted bow"
    assert answer["savings_goal"]["target"] == 1_000_000_000
    assert answer["savings_goal"]["created_at"]
    assert answer["webhook_configured"] is True


def test_desktop_settings_need_a_session(client: TestClient) -> None:
    assert client.get("/api/desktop-settings").status_code == 401


def test_the_desktop_can_tick_quests_for_a_named_character(
    signed_in: TestClient, settings: Settings
) -> None:
    repository = _seed(settings)
    bare, headers = _desktop(signed_in)

    response = bare.post(
        "/api/quests",
        json={"quest_names": ["Cook's Assistant"], "completed": True, "account_hash": "abc123"},
        headers=headers,
    )

    assert response.status_code == 200
    assert "Cook's Assistant" in repository.list_completed_quests("abc123")


def test_editing_a_goal_keeps_when_it_started(signed_in: TestClient) -> None:
    signed_in.post("/api/savings-goal", json={"label": "Bow", "target": 1_000})
    bare, headers = _desktop(signed_in)
    started = bare.get("/api/desktop-settings", headers=headers).json()["savings_goal"]

    signed_in.post("/api/savings-goal", json={"label": "Bigger bow", "target": 2_000})
    edited = bare.get("/api/desktop-settings", headers=headers).json()["savings_goal"]

    assert edited["label"] == "Bigger bow"
    assert edited["target"] == 2_000
    assert edited["created_at"] == started["created_at"]


def test_a_cleared_goal_starts_fresh_when_set_again(signed_in: TestClient) -> None:
    signed_in.post("/api/savings-goal", json={"label": "Bow", "target": 1_000})
    signed_in.delete("/api/savings-goal")
    signed_in.post("/api/savings-goal", json={"label": "Bow", "target": 1_000})
    bare, headers = _desktop(signed_in)

    assert bare.get("/api/desktop-settings", headers=headers).json()["savings_goal"]["created_at"]
