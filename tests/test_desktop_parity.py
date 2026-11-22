from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.market_history import history_stats
from osrs_toolkit.models import TimeseriesPoint
from osrs_toolkit.net_worth_attribution import net_worth_composition
from osrs_toolkit.reports import REPORTS, ReportContext
from osrs_toolkit.website_settings import WebsiteSettingsClient, parse_settings

HASH = "account-1"
NAME = "Wolk"


def _snapshot(captured_at: datetime, coins: int, bones: int = 0) -> LoadoutSnapshot:
    bank = [LoadoutItem(995, "Coins", coins, 1)]
    if bones:
        bank.append(LoadoutItem(536, "Dragon bones", bones, 2_000))
    return LoadoutSnapshot(
        account_hash=HASH,
        account_name=NAME,
        captured_at=captured_at.isoformat(),
        equipment=(LoadoutItem(4151, "Abyssal whip", 1, 1_500_000),),
        inventory=(),
        bank=tuple(bank),
        skills={"Attack": 75},
    )


def test_settings_from_the_website_are_read_with_alerts_and_goal() -> None:
    settings = parse_settings(
        {
            "watchlist": [
                {"item_id": 4151, "target_price": 1_600_000, "direction": "above"},
                {"item_id": 536, "target_price": None, "direction": None},
                {"item_id": "junk"},
            ],
            "savings_goal": {"label": "Bow", "target": 5, "created_at": "2026-11-01"},
            "webhook_configured": True,
        }
    )

    assert settings is not None
    assert settings.watched_ids == {4151, 536}
    alert = settings.alert_for(4151)
    assert alert is not None and alert.target_price == 1_600_000
    assert settings.alert_for(536) is None
    assert settings.savings_goal is not None and settings.savings_goal.label == "Bow"
    assert settings.webhook_configured


def test_an_unusable_reply_is_not_mistaken_for_an_empty_watchlist() -> None:
    assert parse_settings(None) is None
    assert parse_settings({"detail": "Not signed in"}) is None


class _Client:
    configured = True

    def __init__(self, answer: object = None, error: str | None = None) -> None:
        self.answer = answer
        self.last_error = error
        self.calls: list[tuple[str, str, object]] = []

    def get(self, path: str, **_params: object) -> object:
        self.calls.append(("GET", path, None))
        return self.answer

    def post(self, path: str, body: object) -> object:
        self.calls.append(("POST", path, body))
        return self.answer

    def put(self, path: str, body: object) -> object:
        self.calls.append(("PUT", path, body))
        return self.answer

    def delete(self, path: str) -> object:
        self.calls.append(("DELETE", path, None))
        return self.answer


def test_writes_go_to_the_websites_own_endpoints() -> None:
    client = _Client(answer={"ok": True})
    website = WebsiteSettingsClient(client)  # type: ignore[arg-type]

    assert website.watch(4151)
    assert website.set_alert(4151, 1_600_000, "above")
    assert website.set_quests(HASH, {"Cook's Assistant"}, True)
    assert website.remove_event("loot", "kill/1")

    assert client.calls == [
        ("POST", "/api/watchlist", {"item_id": 4151}),
        ("PUT", "/api/watchlist/4151/alert", {"target_price": 1_600_000, "direction": "above"}),
        (
            "POST",
            "/api/quests",
            {"quest_names": ["Cook's Assistant"], "completed": True, "account_hash": HASH},
        ),
        ("DELETE", "/api/loot-log/kill%2F1", None),
    ]


def test_watching_something_already_watched_counts_as_done() -> None:
    client = _Client(answer=None, error="Abyssal whip is already on your watchlist.")

    assert WebsiteSettingsClient(client).watch(4151)  # type: ignore[arg-type]


def test_a_refused_write_reports_the_websites_reason() -> None:
    client = _Client(answer=None, error="Add a Discord webhook on your profile first.")
    website = WebsiteSettingsClient(client)  # type: ignore[arg-type]

    assert not website.set_alert(4151, 1, "below")
    assert website.last_error == "Add a Discord webhook on your profile first."


def test_composition_splits_cash_carried_bank_and_in_flight() -> None:
    parts = net_worth_composition(_snapshot(datetime.now(UTC), 500_000, bones=100), 300_000)

    assert [(part.label, part.value) for part in parts] == [
        ("Cash", 500_000),
        ("Carried gear", 1_500_000),
        ("Bank", 200_000),
        ("GE in-flight", 300_000),
    ]
    assert round(sum(part.percent for part in parts)) == 100
    assert net_worth_composition(None, 0) == []


def test_history_stats_average_volume_by_weekday() -> None:
    monday = int(datetime(2026, 11, 16, tzinfo=UTC).timestamp())
    points = [
        TimeseriesPoint(monday, 110, 100, 10, 20),
        TimeseriesPoint(monday + 7 * 86_400, 120, 100, 30, 10),
        TimeseriesPoint(monday + 86_400, None, 100, 5, 5),
    ]

    stats, by_weekday = history_stats(points)

    assert stats is not None
    assert stats.average_spread == 15
    assert stats.total_volume == 80
    assert [(row.day, row.average_volume) for row in by_weekday] == [
        ("Monday", 35),
        ("Tuesday", 10),
    ]
    assert history_stats([]) == (None, [])


def _context(tmp_path: Path) -> ReportContext:
    journal = JournalRepository(tmp_path / "journal.db")
    now = datetime.now(UTC)
    for days_ago, coins in ((80, 100_000), (20, 200_000), (5, 300_000), (0, 400_000)):
        journal.save_loadout_snapshot(_snapshot(now - timedelta(days=days_ago), coins))
    journal.add_synced_trade(
        SyncedTrade(
            event_id="fill-1",
            occurred_at=now.isoformat(),
            event_type="ge_fill",
            account_hash=HASH,
            account_name=NAME,
            counterparty=None,
            direction="buy",
            metadata={},
            items=(SyncedItem("received", 536, "Dragon bones", 1, 2_000),),
        )
    )
    for minute in range(3):
        journal.add_npc_loot_event(
            NpcLootRecord(
                event_id=f"kill-{minute}",
                occurred_at=(now - timedelta(hours=2) + timedelta(minutes=minute)).isoformat(),
                account_hash=HASH,
                account_name=NAME,
                npc_name="Vorkath",
                items=(LoadoutItem(536, "Dragon bones", 2, 2_000),),
            )
        )
    return ReportContext(journal, journal.get_latest_loadout_snapshot(), {}, [], now)


def test_the_net_worth_window_follows_the_choice(tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = REPORTS["Net Worth"]

    assert definition.default_choice in definition.choices(context)  # type: ignore[misc]
    week = definition.build(context, "Compare with 7 days ago")[0]
    quarter = definition.build(context, "Compare with 90 days ago")[0]

    assert week.hint.endswith("(+200,000).")
    assert quarter.hint.endswith("(+300,000).")


def test_picking_a_trip_shows_its_loot_by_monster(tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = REPORTS["Trips"]
    choices = definition.choices(context)  # type: ignore[misc]

    sections = definition.build(context, choices[0])

    titles = [section.title for section in sections]
    assert "Loot by monster" in titles
    loot = next(section for section in sections if section.title == "Loot by monster")
    assert loot.rows[0][:2] == ("Vorkath", "3")
    assert titles[-1] == "Trip ledger"


def test_activity_rows_carry_their_event_ids_for_removal(tmp_path: Path) -> None:
    context = _context(tmp_path)

    section = REPORTS["Activity"].build(context, None)[0]

    assert REPORTS["Activity"].remove_kind == "activity"
    assert section.row_keys == ("fill-1",)


def test_the_overview_charts_net_worth_and_shows_composition(tmp_path: Path) -> None:
    context = _context(tmp_path)

    sections = {section.title: section for section in REPORTS["Overview"].build(context, None)}

    assert len(sections["Net worth over time"].chart) == 4
    assert [row[0] for row in sections["Net worth composition"].rows] == [
        "Cash",
        "Carried gear",
    ]
