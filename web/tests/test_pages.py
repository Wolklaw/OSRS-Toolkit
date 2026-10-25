from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    OfferCancelled,
    OfferOpened,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.runelite_sync import GEOfferSlot

from toolkit_web.config import Settings


def test_the_landing_page_shows_a_sign_in_link_when_signed_out(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" in response.text
    assert "/auth/discord/login" in response.text


def test_the_landing_page_says_the_plugin_is_on_the_hub(client: TestClient):
    page = client.get("/").text

    assert "Plugin Hub" in page
    assert 'href="/plugin"' in page


def test_the_root_page_shows_the_dashboard_once_signed_in(signed_in: TestClient):
    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Sign in with Discord" not in response.text
    assert "Wolklaw" in response.text


def test_an_unpaired_account_is_offered_the_connect_flow(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Not set up" in response.text
    assert "Get a token" in response.text


def test_a_paired_account_is_not_asked_to_connect_again(signed_in: TestClient):
    from unittest.mock import patch

    with patch("toolkit_web.routes.account.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    response = signed_in.get("/")

    assert "Get a token" not in response.text


def test_an_empty_journal_shows_the_empty_state(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Nothing tracked yet" in response.text
    assert "Undefined" not in response.text


def test_a_tracked_position_appears_on_the_dashboard_with_its_numbers(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 2, target_buy=1_000_000, target_sell=1_200_000)

    response = signed_in.get("/")

    assert "Abyssal whip" in response.text
    assert "2,000,000" in response.text


def test_net_worth_shows_the_most_recent_reading_not_an_older_one(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    for value in (1_000_000, 5_000_000):
        repository.save_loadout_snapshot(_bare_snapshot(total_value=value))

    response = signed_in.get("/")

    assert '5,000,000<span class="unit">gp</span>' in response.text
    assert '1,000,000<span class="unit">gp</span>' not in response.text


def test_the_performance_page_renders_with_no_history_yet(signed_in: TestClient):
    response = signed_in.get("/performance")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Nothing realized in this period yet" in response.text


def test_the_performance_page_shows_a_completed_positions_realized_profit(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_200_000
    )
    repository.update_tracked(position_id, "Completed", 1_000_000, 1_200_000)

    response = signed_in.get("/performance")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Balanced (1" in response.text


def test_the_performance_page_groups_repeat_items_once_there_are_two(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    for _ in range(2):
        position_id = repository.track(
            4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_200_000
        )
        repository.update_tracked(position_id, "Completed", 1_000_000, 1_200_000)

    response = signed_in.get("/performance")

    assert "Abyssal whip" in response.text


def test_pvm_shows_supplies_spend_grouped_by_item(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    position_id = repository.track(
        1755, "Prayer potion(4)", 10, target_buy=10_000, target_sell=10_000
    )
    repository.update_tracked(position_id, "Supplies", 10_000, None, buy_fills=[(10, 10_000)])

    response = signed_in.get("/pvm")

    assert response.status_code == 200
    assert "Undefined" not in response.text
    assert "Prayer potion(4)" in response.text
    assert "100,000" in response.text


def test_the_item_history_page_degrades_cleanly_when_the_wiki_is_unreachable(
    signed_in: TestClient,
):
    from unittest.mock import patch

    from osrs_toolkit.market import MarketDataError

    with patch(
        "toolkit_web.market_cache.WikiMarketClient.fetch_timeseries",
        side_effect=MarketDataError("offline"),
    ):
        response = signed_in.get("/item/4151")

    assert response.status_code == 200
    assert "No price history available" in response.text


def test_the_item_history_page_renders_a_chart_when_prices_are_available(
    signed_in: TestClient,
):
    from unittest.mock import patch

    from osrs_toolkit.models import TimeseriesPoint

    points = [
        TimeseriesPoint(timestamp=1_000, average_high=100, average_low=90),
        TimeseriesPoint(timestamp=2_000, average_high=110, average_low=95),
    ]
    with patch("toolkit_web.market_cache.WikiMarketClient.fetch_timeseries", return_value=points):
        response = signed_in.get("/item/4151")

    assert response.status_code == 200
    assert "<svg" in response.text
    assert "No price history available" not in response.text


def test_the_item_history_page_shows_volume_and_spread_stats(signed_in: TestClient):
    from unittest.mock import patch

    from osrs_toolkit.models import TimeseriesPoint

    points = [
        TimeseriesPoint(
            timestamp=1755392400,
            average_high=100,
            average_low=80,
            high_volume=100,
            low_volume=50,
        ),
        TimeseriesPoint(
            timestamp=1755478800,
            average_high=120,
            average_low=100,
            high_volume=200,
            low_volume=100,
        ),
    ]
    with patch("toolkit_web.market_cache.WikiMarketClient.fetch_timeseries", return_value=points):
        response = signed_in.get("/item/4151")

    assert response.status_code == 200
    assert "Average spread: 20 gp" in response.text
    assert "Total volume shown: 450" in response.text
    assert "Volume by day of week" in response.text
    assert "Sunday" in response.text
    assert "Monday" in response.text


def test_csv_export_includes_tracked_and_manual_trades(signed_in: TestClient, settings: Settings):
    repository = JournalRepository(settings.journal_path("111"))
    repository.track(4151, "Abyssal whip", 1, target_buy=1_000_000, target_sell=1_100_000)
    repository.add("Dragon bones", quantity=10, buy_price=2_000, sell_price=2_200)

    response = signed_in.get("/api/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Abyssal whip" in response.text
    assert "Dragon bones" in response.text


def _bare_snapshot(total_value: int):
    from osrs_toolkit.journal import LoadoutItem, LoadoutSnapshot

    return LoadoutSnapshot(
        account_hash="abc123",
        account_name="Wolklaw",
        captured_at=datetime.now(UTC).isoformat(),
        equipment=(
            LoadoutItem(item_id=995, item_name="Coins", quantity=total_value, unit_value=1),
        ),
        inventory=(),
        bank=(),
        skills={},
    )


def test_the_wordmark_is_one_flex_item_so_it_does_not_read_rune_scope(signed_in: TestClient):
    page = signed_in.get("/").text

    assert '<span class="wordmark">Rune<em>Scope</em></span>' in page
    assert '<span class="mark">RS</span>Rune' not in page


def test_the_signed_out_header_wordmark_is_wrapped_too(client: TestClient):
    page = client.get("/about").text

    assert '<span class="wordmark">Rune<em>Scope</em></span>' in page
    assert '<span class="mark">RS</span>Rune' not in page


def test_the_performance_page_reports_slippage_once_an_item_has_enough_fills(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    for index in range(3):
        repository.add_synced_trade(
            SyncedTrade(
                event_id=f"slip-{index}",
                occurred_at=datetime.now(UTC).isoformat(),
                event_type="ge_fill",
                account_hash="abc123",
                account_name="Wolklaw",
                counterparty=None,
                direction="buy",
                metadata={"offer_price": 1000},
                items=(
                    SyncedItem("given", 995, "Coins", 9_000, 1),
                    SyncedItem("received", 4151, "Abyssal whip", 10, 900),
                ),
            )
        )

    page = signed_in.get("/performance").text

    assert "Price slippage" in page
    assert "Abyssal whip" in page
    assert "+3,000" in page


def test_the_performance_page_lists_cancelled_offers_with_the_wait(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    now = datetime.now(UTC)
    repository.record_offer_event(
        OfferOpened(
            "o1", "h", "n", (now - timedelta(minutes=6)).isoformat(), "buy", 1, "Coal", 100, 50
        ),
        "Overnight",
    )
    repository.record_offer_event(
        OfferCancelled("c1", "h", "n", now.isoformat(), "buy", 1, "Coal", 100, 50), "Overnight"
    )

    page = signed_in.get("/performance").text

    assert "Cancelled offers" in page
    assert "Overnight" in page
    assert "6 min" in page


def test_the_performance_page_reports_fill_times_once_an_item_has_three_completed_offers(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    start = datetime.now(UTC) - timedelta(hours=3)
    for index in range(3):
        opened_at = start + timedelta(minutes=index * 20)
        repository.record_offer_event(
            OfferOpened(
                f"o{index}", "h", "n", opened_at.isoformat(), "buy", 4151, "Abyssal whip", 1000, 10
            ),
            None,
        )
        repository.add_synced_trade(
            SyncedTrade(
                event_id=f"fill-{index}",
                occurred_at=(opened_at + timedelta(minutes=8)).isoformat(),
                event_type="ge_fill",
                account_hash="h",
                account_name="n",
                counterparty=None,
                direction="buy",
                metadata={"offer_price": 1000, "total_quantity": 10, "offer_state": "BOUGHT"},
                items=(
                    SyncedItem("given", 995, "Coins", 10_000, 1),
                    SyncedItem("received", 4151, "Abyssal whip", 10, 1000),
                ),
            )
        )

    page = signed_in.get("/performance").text

    assert "How long your offers take to fill" in page
    assert "8 min" in page


def test_the_trip_ledger_nets_a_trips_loot_against_the_supplies_that_left(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    now = datetime.now(UTC)

    def snapshot(minutes_ago: int, brews: int) -> LoadoutSnapshot:
        return LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=(now - timedelta(minutes=minutes_ago)).isoformat(),
            equipment=(),
            inventory=(LoadoutItem(100, "Brew", brews, 10),),
            bank=(),
            skills={},
        )

    repository.save_loadout_snapshot(snapshot(60, 40))
    for index, minutes_ago in enumerate((30, 20)):
        repository.add_npc_loot_event(
            NpcLootRecord(
                f"loot-{index}",
                (now - timedelta(minutes=minutes_ago)).isoformat(),
                "abc123",
                "Wolklaw",
                "Vorkath",
                (LoadoutItem(1, "Bones", 1, 5_000),),
            )
        )
    repository.save_loadout_snapshot(snapshot(5, 10))
    signed_in.cookies.set("active_character", "abc123")

    page = signed_in.get("/trips").text

    assert "Vorkath" in page
    assert "10,000" in page
    assert "+9,700" in page


def test_the_trip_ledger_asks_for_a_character_when_none_is_selected(signed_in: TestClient):
    assert "No character selected" in signed_in.get("/trips").text


def test_the_drop_rates_page_lists_monsters_and_how_long_an_item_has_been_dry(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    now = datetime.now(UTC)
    for index in range(5):
        items = [LoadoutItem(1, "Dragon bones", 2, 2_000)]
        if index == 0:
            items.append(LoadoutItem(9, "Draconic visage", 1, 5_000_000))
        repository.add_npc_loot_event(
            NpcLootRecord(
                f"drop-{index}",
                (now - timedelta(minutes=10 - index)).isoformat(),
                "abc123",
                "Wolklaw",
                "Vorkath",
                tuple(items),
            )
        )
    signed_in.cookies.set("active_character", "abc123")

    page = signed_in.get("/drops").text

    assert "Vorkath" in page
    assert "Draconic visage" in page
    assert "20.00%" in page


def test_the_drop_rates_page_asks_for_a_character_when_none_is_selected(signed_in: TestClient):
    assert "No character selected" in signed_in.get("/drops").text


def test_the_net_worth_page_explains_a_change_between_two_snapshots(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    now = datetime.now(UTC)

    def snapshot(days_ago: int, whip_price: int) -> LoadoutSnapshot:
        return LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=(now - timedelta(days=days_ago)).isoformat(),
            equipment=(),
            inventory=(LoadoutItem(7, "Abyssal whip", 2, whip_price),),
            bank=(),
            skills={},
        )

    repository.save_loadout_snapshot(snapshot(10, 1_000_000))
    repository.save_loadout_snapshot(snapshot(0, 1_200_000))
    signed_in.cookies.set("active_character", "abc123")

    page = signed_in.get("/net-worth").text

    assert "Price movement" in page
    assert "+400,000" in page
    assert "Abyssal whip" in page


def test_the_net_worth_page_asks_for_two_snapshots_when_it_has_one(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    repository.save_loadout_snapshot(
        LoadoutSnapshot(
            account_hash="abc123",
            account_name="Wolklaw",
            captured_at=datetime.now(UTC).isoformat(),
            equipment=(),
            inventory=(),
            bank=(),
            skills={},
        )
    )
    signed_in.cookies.set("active_character", "abc123")

    assert "Two bank snapshots needed" in signed_in.get("/net-worth").text


def _stale_position(settings: Settings, item_id: int, minutes_old: int) -> None:
    path = settings.journal_path("111")
    repository = JournalRepository(path)
    position_id = repository.track(item_id, "Coal", 100, 150, 200, account_hash="abc123")
    created = (datetime.now(UTC) - timedelta(minutes=minutes_old)).isoformat(timespec="seconds")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE tracked_trades SET created_at = ? WHERE position_id = ?",
            (created, position_id),
        )


def _dashboard(signed_in: TestClient, state: str, slots: dict) -> str:
    signed_in.cookies.set("active_character", "abc123")
    with (
        patch("toolkit_web.routes.pages.plugin_activity", return_value=state),
        patch("toolkit_web.routes.pages.live_offer_state", return_value=(slots, None)),
    ):
        return signed_in.get("/").text


def test_a_position_with_no_offer_in_a_live_grand_exchange_is_flagged(
    signed_in: TestClient, settings: Settings
):
    _stale_position(settings, 453, minutes_old=120)

    page = _dashboard(signed_in, "live", {})

    assert "Not in GE" in page
    assert "not in my Grand Exchange (1)" in page
    assert 'data-not-in-ge="1"' in page


def test_a_fresh_position_or_an_idle_plugin_is_never_flagged(
    signed_in: TestClient, settings: Settings
):
    _stale_position(settings, 453, minutes_old=2)
    assert "Not in GE" not in _dashboard(signed_in, "live", {})

    _stale_position(settings, 454, minutes_old=300)
    assert "Not in GE" not in _dashboard(signed_in, "idle", {})


def test_a_position_with_a_matching_live_offer_is_not_flagged(
    signed_in: TestClient, settings: Settings
):
    _stale_position(settings, 453, minutes_old=120)
    slot = GEOfferSlot(
        slot=0,
        item_id=453,
        item_name="Coal",
        offer_price=150,
        total_quantity=100,
        quantity_filled=10,
        spent_gp=1_500,
        state="BUYING",
    )

    assert "Not in GE" not in _dashboard(signed_in, "live", {0: slot})


def test_the_dashboard_files_supplies_and_finished_positions_apart_from_open_ones(
    signed_in: TestClient, settings: Settings
):
    repository = JournalRepository(settings.journal_path("111"))
    open_id = repository.track(1, "Open item", 10, 100, 150)
    supplies_id = repository.track(2, "Supply item", 10, 100, 150)
    repository.update_tracked(supplies_id, "Supplies", None, None, None, [(10, 100)])
    done_id = repository.track(3, "Done item", 10, 100, 150)
    repository.update_tracked(done_id, "Completed", 100, 150, [(10, 150)], [(10, 100)])

    page = signed_in.get("/").text

    assert "Open (1)" in page
    assert "Supplies (1)" in page
    assert "Finished (1)" in page
    assert page.count('data-view="open"') == 1
    assert page.count('data-view="supplies"') == 1
    assert page.count('data-view="closed"') == 1
    assert open_id and supplies_id and done_id
