from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.journal import (
    ContainerSnapshot,
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    OfferOpened,
    PlayerDeathRecord,
    ProgressRecord,
    SkillsPoint,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.journal_mirror import PLUGIN_DATA_PATH, JournalMirror

HASH = "account-1"
NAME = "Wolk"
BASE = datetime(2026, 11, 1, 12, tzinfo=UTC)
BONES = LoadoutItem(item_id=536, item_name="Dragon bones", quantity=2, unit_value=2_000)


def _at(minutes: int) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _kill(index: int) -> NpcLootRecord:
    return NpcLootRecord(
        event_id=f"kill-{index}",
        occurred_at=_at(index),
        account_hash=HASH,
        account_name=NAME,
        npc_name="Vorkath",
        items=(BONES,),
    )


def _fill(index: int) -> SyncedTrade:
    return SyncedTrade(
        event_id=f"fill-{index}",
        occurred_at=_at(index),
        event_type="ge_fill",
        account_hash=HASH,
        account_name=NAME,
        counterparty=None,
        direction="buy",
        metadata={"offer_price": 2_000},
        items=(
            SyncedItem("given", 995, "Coins", 4_000, 1),
            SyncedItem("received", 536, "Dragon bones", 2, 2_000),
        ),
    )


def _snapshot(minutes: int, coins: int) -> LoadoutSnapshot:
    return LoadoutSnapshot(
        account_hash=HASH,
        account_name=NAME,
        captured_at=_at(minutes),
        equipment=(),
        inventory=(),
        bank=(LoadoutItem(995, "Coins", coins, 1),),
        skills={"Attack": 75},
        xp={"Attack": 1_210_421},
    )


def _website(tmp_path: Path) -> JournalRepository:
    website = JournalRepository(tmp_path / "website.db")
    website.add_synced_trade(_fill(0))
    website.add_npc_loot_event(_kill(1))
    website.add_player_death_event(
        PlayerDeathRecord("death-1", _at(2), HASH, NAME, True, (), (BONES,))
    )
    website.record_offer_event(
        OfferOpened("offer-1", HASH, NAME, _at(0), "buy", 536, "Dragon bones", 2_000, 2)
    )
    website.save_loadout_snapshot(_snapshot(0, 1_000_000))
    website.save_loadout_snapshot(_snapshot(30, 2_000_000))
    website.save_skills_snapshot(SkillsPoint(HASH, NAME, _at(31), {"Attack": 76}))
    website.save_progress(
        ProgressRecord(HASH, NAME, _at(5), 12, {"Cook's Assistant": "finished"}, {}, 0, {}, {})
    )
    website.save_container_snapshot(
        ContainerSnapshot(HASH, NAME, _at(6), {"looting_bag": (BONES,)})
    )
    website.set_quests_completed(HASH, ["Cook's Assistant"], True)
    return website


def _pull(website: JournalRepository, desktop: JournalRepository) -> int:
    cursors = desktop.plugin_mirror_cursors()
    total = 0
    for _ in range(50):
        payload = website.plugin_export(cursors)
        total += desktop.plugin_apply(payload)
        cursors = {**cursors, **payload["cursors"]}
        desktop.set_plugin_mirror_cursors(cursors)
        if not payload["more"]:
            break
    return total


def test_everything_the_plugin_sent_the_website_reaches_the_desktop(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")

    assert _pull(website, desktop) > 0

    assert [t.event_id for t in desktop.list_synced_trades()] == ["fill-0"]
    assert desktop.list_synced_trades()[0].received[0].item_name == "Dragon bones"
    loot = desktop.list_npc_loot_events(account_hash=HASH)
    assert [(event.event_id, event.items) for event in loot] == [("kill-1", (BONES,))]
    deaths = desktop.list_player_death_events(account_hash=HASH)
    assert [(event.event_id, event.skulled) for event in deaths] == [("death-1", True)]
    assert [event.event_id for event in desktop.list_offer_events(HASH)] == ["offer-1"]
    assert len(desktop.list_loadout_snapshots(HASH)) == 2
    newest = desktop.get_latest_loadout_snapshot()
    assert newest is not None and newest.total_value == 2_000_000
    assert newest.xp == {"Attack": 1_210_421}
    assert [point.total_value for point in desktop.list_net_worth_history(HASH)] == [
        1_000_000,
        2_000_000,
    ]
    assert desktop.list_skills_history(HASH)[-1].skills == {"Attack": 76}
    progress = desktop.get_progress(HASH)
    assert progress is not None and progress.quest_points == 12
    assert desktop.list_container_items(HASH)["looting_bag"] == (BONES,)
    assert desktop.list_completed_quests(HASH) == {"Cook's Assistant"}


def test_a_second_pass_brings_nothing_twice(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    _pull(website, desktop)

    assert _pull(website, desktop) == 0
    assert len(desktop.list_npc_loot_events(account_hash=HASH)) == 1
    assert len(desktop.list_net_worth_history(HASH)) == 2


def test_only_what_is_new_arrives_after_the_first_pass(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    _pull(website, desktop)

    website.add_npc_loot_event(_kill(9))
    payload = website.plugin_export(desktop.plugin_mirror_cursors())

    assert [row["event_id"] for row in payload["rows"]["npc_loot_events"]][-1] == "kill-9"
    assert payload["rows"]["loadout_snapshot_history"] == []
    assert desktop.plugin_apply(payload) == 1


def test_history_the_desktop_already_holds_is_never_duplicated(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    desktop.add_synced_trade(_fill(0))

    _pull(website, desktop)

    assert len(desktop.list_synced_trades()) == 1
    assert len(desktop.list_synced_trades()[0].items) == 2


def test_a_removal_on_the_website_reaches_the_desktop(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    _pull(website, desktop)

    website.delete_npc_loot_event("kill-1")
    website.delete_synced_trade("fill-0")
    _pull(website, desktop)

    assert desktop.list_npc_loot_events(account_hash=HASH) == []
    assert desktop.list_synced_trades() == []


def test_a_quest_unticked_on_the_website_is_unticked_here(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    _pull(website, desktop)

    website.set_quests_completed(HASH, ["Cook's Assistant"], False)
    _pull(website, desktop)

    assert desktop.list_completed_quests(HASH) == frozenset()


def test_large_histories_arrive_in_pages(tmp_path: Path) -> None:
    website = JournalRepository(tmp_path / "website.db")
    for index in range(JournalRepository.MIRROR_PAGE_SIZE + 5):
        website.add_npc_loot_event(_kill(index))
    desktop = JournalRepository(tmp_path / "desktop.db")

    first = website.plugin_export({})
    assert first["more"] is True
    _pull(website, desktop)

    assert len(desktop.list_npc_loot_events(account_hash=HASH)) == (
        JournalRepository.MIRROR_PAGE_SIZE + 5
    )


def test_an_older_progress_record_does_not_overwrite_a_newer_one(tmp_path: Path) -> None:
    website = _website(tmp_path)
    desktop = JournalRepository(tmp_path / "desktop.db")
    desktop.save_progress(ProgressRecord(HASH, NAME, _at(99), 40, {}, {}, 0, {}, {}))

    _pull(website, desktop)

    progress = desktop.get_progress(HASH)
    assert progress is not None and progress.quest_points == 40


def test_a_website_without_the_endpoint_leaves_the_mirror_working(tmp_path: Path) -> None:
    class OldWebsite:
        configured = True

        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, path: str, **_params: object) -> object | None:
            self.calls.append(path)
            return {"version": "", "counts": {}} if path == "/api/journal/version" else None

        def post(self, path: str, _body: object) -> object | None:
            self.calls.append(path)
            return None

    website = OldWebsite()
    result = JournalMirror(JournalRepository(tmp_path / "desktop.db"), website).sync()

    assert result.reached
    assert result.plugin_rows == 0
    assert website.calls.count(PLUGIN_DATA_PATH) == 1


def test_the_mirror_pulls_plugin_data_through_the_website(tmp_path: Path) -> None:
    remote = _website(tmp_path)

    class Website:
        configured = True

        def get(self, path: str, **params: object) -> object | None:
            if path == "/api/journal/version":
                return remote.sync_version()
            if path == "/api/journal/pull":
                return remote.sync_export(since=params.get("since") or None)
            return None

        def post(self, path: str, body: dict) -> object | None:
            if path == PLUGIN_DATA_PATH:
                return remote.plugin_export(body.get("cursors"))
            if path == "/api/journal/push":
                return {"ok": True, **remote.sync_apply(body)}
            return None

    desktop = JournalRepository(tmp_path / "desktop.db")
    mirror = JournalMirror(desktop, Website())

    first = mirror.sync()
    second = mirror.sync()

    assert first.plugin_rows > 0 and first.changed
    assert "from the plugin" in first.describe()
    assert second.plugin_rows == 0
    assert len(desktop.list_npc_loot_events(account_hash=HASH)) == 1
