from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QTabWidget

from osrs_toolkit import __version__
from osrs_toolkit.account import PlayerProfile
from osrs_toolkit.app import (
    MainWindow,
    RuneLiteConnectionDialog,
    SettingsDialog,
    UpdateTrackedTradeDialog,
)
from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.journal import LoadoutItem, LoadoutSnapshot, SyncedItem, SyncedTrade
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.runelite_sync import RuneLiteConnectionStatus, RuneLiteSyncImporter

WINDOW_SIZE = (1_920, 1_000)


def save_widget(widget: object, path: Path, app: QApplication) -> None:
    """Render a real Qt widget after its layout and paint events have settled.

    Two things here keep the committed images stable. Opening a dialog can leave the main
    window a little taller than it started, so its size is re-asserted before every grab
    rather than only once at start-up. And the grab inherits the device pixel ratio of
    whichever monitor is running the capture, so a developer at 125% scaling would otherwise
    rewrite every screenshot at a different resolution than one at 100%.
    """
    if isinstance(widget, MainWindow):
        widget.resize(*WINDOW_SIZE)
    app.processEvents()
    widget.repaint()  # type: ignore[attr-defined]
    app.processEvents()
    pixmap = widget.grab()  # type: ignore[attr-defined]
    if pixmap.devicePixelRatio() != 1.0:
        logical = pixmap.size() / pixmap.devicePixelRatio()
        pixmap.setDevicePixelRatio(1.0)
        pixmap = pixmap.scaled(
            logical,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def show_page(window: MainWindow, name: str) -> None:
    """Select a sidebar page by name.

    Row numbers used to be hardcoded here, so inserting a page silently shifted every
    screenshot after it into the wrong file.
    """
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index(name))


def show_journal_tab(window: MainWindow, name: str) -> None:
    """Select a Trade Journal tab by its visible label, for the same reason show_page
    looks pages up by name instead of a hardcoded index."""
    tabs = window.journal_tabs
    index = next(i for i in range(tabs.count()) if tabs.tabText(i) == name)
    tabs.setCurrentIndex(index)


def backdate(
    window: MainWindow, position_id: int, *, opened_hours_ago: float, held_hours: float | None
) -> None:
    """Give a demo position a believable lifetime.

    The app records real timestamps, but a journal seeded inside one second would show
    every flip opening and closing at the same instant — which the Performance page
    correctly reports as an unknown hold time rather than an instantaneous one.
    """
    opened = datetime.now(UTC) - timedelta(hours=opened_hours_ago)
    finished = (
        None if held_hours is None
        else (opened + timedelta(hours=held_hours)).isoformat(timespec="seconds")
    )
    with window._journal._connect() as connection:
        connection.execute(
            """
            UPDATE tracked_trades
            SET created_at = ?,
                completed_at = CASE WHEN completed_at IS NULL THEN NULL ELSE ? END
            WHERE position_id = ?
            """,
            (opened.isoformat(timespec="seconds"), finished, position_id),
        )


def add_performance_examples(window: MainWindow) -> None:
    """Finished flips across all three strategies, so the Performance page has something
    to compare. Deliberately a mix: Quick turns over fastest, Overnight makes the most per
    flip, and one Balanced position missed its sell target."""
    quick_id = window._journal.track(1515, "Yew logs", 9_000, 320, 345, "Quick (up to 1h)")
    window._journal.update_tracked(
        quick_id, "Completed", None, None, [(9_000, 344)], [(9_000, 318)]
    )
    backdate(window, quick_id, opened_hours_ago=6, held_hours=0.5)

    missed_id = window._journal.track(536, "Dragon bones", 2_400, 2_700, 2_850, "Balanced (1–4h)")
    window._journal.update_tracked(
        missed_id, "Completed", None, None, [(2_400, 2_781)], [(2_400, 2_744)]
    )
    backdate(window, missed_id, opened_hours_ago=30, held_hours=3.4)

    overnight_id = window._journal.track(
        13441, "Anglerfish", 3_000, 2_180, 2_290, "Overnight (8–12h)"
    )
    window._journal.update_tracked(
        overnight_id, "Completed", None, None, [(3_000, 2_296)], [(3_000, 2_172)]
    )
    backdate(window, overnight_id, opened_hours_ago=40, held_hours=10.5)


def add_journal_examples(window: MainWindow) -> int:
    """Populate a private demo journal that shows every important trade state."""
    strategy = window.strategy.currentText()

    listed_id = window._journal.track(
        1519, "Willow branch", 1_333, 117, 127, strategy
    )
    window._journal.update_tracked(listed_id, "Listed for sale", 118, None)

    cancelled_id = window._journal.track(
        4151, "Abyssal whip", 1, 1_500_000, 1_550_000, strategy
    )
    window._journal.update_tracked(cancelled_id, "Cancelled", None, None, [])

    loss_id = window._journal.track(
        2353, "Steel bar", 1_200, 450, 500, strategy
    )
    window._journal.update_tracked(
        loss_id,
        "Completed",
        450,
        None,
        [(1_200, 440)],
    )

    variable_id = window._journal.track(
        21316, "Amethyst broad bolts", 1_000, 195, 212, strategy
    )
    window._journal.update_tracked(
        variable_id,
        "Completed",
        195,
        None,
        [(500, 212), (500, 202)],
    )

    if window._flips:
        overnight = window._flips[0]
        overnight_id = window._journal.track(
            overnight.item_id,
            overnight.name,
            overnight.suggested_quantity,
            overnight.buy_price,
            overnight.sell_price,
            "Overnight (8–12h)",
        )
        window._journal.review_suggestion(
            overnight_id,
            max(1, overnight.buy_price + 2),
            max(1, overnight.sell_price - 2),
        )

    partial_id = window._journal.track(
        19478, "Dragon dart tip", 1_000, 733, 850, strategy
    )
    window._journal.update_tracked(
        partial_id,
        "Partially sold",
        738,
        None,
        [(500, 842), (300, 790)],
    )

    window._journal.add("Rune platebody", 12, 37_500, 39_250)

    # Spread the demo history over a few days so both the Journal's Date column and the
    # Performance page's hold times read like a real account rather than one busy second.
    backdate(window, listed_id, opened_hours_ago=5, held_hours=None)
    backdate(window, cancelled_id, opened_hours_ago=52, held_hours=1.5)
    backdate(window, loss_id, opened_hours_ago=27, held_hours=3.8)
    backdate(window, variable_id, opened_hours_ago=15, held_hours=2.1)
    backdate(window, partial_id, opened_hours_ago=9, held_hours=None)
    return partial_id


def add_supplies_examples(window: MainWindow) -> None:
    """Quest and skilling buys marked Supplies, so the Plans tab shows them sitting
    alongside ordinary flips without polluting them, and Supplies spend has real totals."""
    lobster_id = window._journal.track(379, "Lobster", 9_000, 152, 152, "Balanced (1–4h)")
    window._journal.update_tracked(lobster_id, "Supplies", None, None, None, [(9_000, 152)])
    backdate(window, lobster_id, opened_hours_ago=18, held_hours=None)

    prayer_id = window._journal.track(
        2_434, "Prayer potion(4)", 1_400, 8_600, 8_600, "Balanced (1–4h)"
    )
    window._journal.update_tracked(prayer_id, "Supplies", None, None, None, [(1_400, 8_612)])
    backdate(window, prayer_id, opened_hours_ago=3, held_hours=None)


def add_buy_limit_example(window: MainWindow) -> int | None:
    """A recent, real synced buy so the Buy limits tab has something inside its rolling
    4-hour window to show. Returns the item_id used, or None if the current market
    snapshot's top flip candidate has no known buy limit to demonstrate against."""
    candidate = window._flips[0]
    limit = window._mappings[candidate.item_id].buy_limit
    if not limit:
        return None
    quantity = max(1, limit // 3)
    trade = SyncedTrade(
        event_id="docs-buy-limit",
        occurred_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds"),
        event_type="ge_fill",
        account_hash="docs-account",
        account_name="Example Player",
        counterparty=None,
        direction="buy",
        metadata={},
        items=(
            SyncedItem("given", 995, "Coins", quantity * candidate.buy_price, 1),
            SyncedItem(
                "received", candidate.item_id, candidate.name, quantity, candidate.buy_price
            ),
        ),
    )
    window._journal.add_synced_trade(trade)
    return candidate.item_id


def add_ge_offer_state(window: MainWindow, sync_root: Path) -> None:
    """A live-looking Grand Exchange: two offers still filling and one bought and sitting
    uncollected, so the dashboard reads like the middle of a real flipping session rather
    than an all-empty one. Written straight to the files the real plugin writes, the same
    way ``read_offer_state`` reads them back — this is not going through a mock."""
    account_hash = "abc123"
    sync_root.mkdir(parents=True, exist_ok=True)
    (sync_root / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": True,
                "account_hash": account_hash,
                "account_name": "Example Player",
                "player_trade_tracking": True,
            }
        ),
        encoding="utf-8",
    )
    state_dir = sync_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    slots = {
        "0": {
            "slot": 0,
            "itemId": 1_515,
            "itemName": "Yew logs",
            "offerPrice": 345,
            "totalQuantity": 9_000,
            "quantityFilled": 5_400,
            "spentGp": 1_863_000,
            "state": "BUYING",
            "offerId": "docs-offer-0",
        },
        "2": {
            "slot": 2,
            "itemId": 536,
            "itemName": "Dragon bones",
            "offerPrice": 2_850,
            "totalQuantity": 2_400,
            "quantityFilled": 2_400,
            "spentGp": 6_840_000,
            "state": "BOUGHT",
            "offerId": "docs-offer-2",
        },
        "5": {
            "slot": 5,
            "itemId": 13_441,
            "itemName": "Anglerfish",
            "offerPrice": 2_290,
            "totalQuantity": 3_000,
            "quantityFilled": 900,
            "spentGp": 2_061_000,
            "state": "SELLING",
            "offerId": "docs-offer-5",
        },
    }
    (state_dir / f"{account_hash}.json").write_text(json.dumps(slots), encoding="utf-8")


def add_savings_goal_example() -> None:
    """A goal a little over a week old, so the realized profit from add_performance_examples
    (all backdated well inside that window) counts toward visible progress."""
    settings = QSettings()
    settings.setValue("savings_goal/label", "Bandos chestplate")
    settings.setValue("savings_goal/target", 5_000_000)
    settings.setValue(
        "savings_goal/created_at",
        (datetime.now(UTC) - timedelta(days=9)).isoformat(timespec="seconds"),
    )


def add_runelite_examples(window: MainWindow) -> None:
    now = datetime.now(UTC)
    examples = (
        SyncedTrade(
            event_id="docs-ge-buy",
            occurred_at=(now - timedelta(minutes=11)).isoformat(timespec="seconds"),
            event_type="ge_fill",
            account_hash="docs-account",
            account_name="Example Player",
            counterparty=None,
            direction="buy",
            metadata={"offer_price": 2_320, "slot": 3, "state": "partial"},
            items=(
                SyncedItem("given", 995, "Coins", 2_250, 1),
                SyncedItem("received", 13441, "Anglerfish", 1, 2_250),
            ),
        ),
        SyncedTrade(
            event_id="docs-ge-sell",
            occurred_at=(now - timedelta(minutes=7)).isoformat(timespec="seconds"),
            event_type="ge_fill",
            account_hash="docs-account",
            account_name="Example Player",
            counterparty=None,
            direction="sell",
            metadata={"offer_price": 1_900, "slot": 3, "state": "finished"},
            items=(
                SyncedItem("given", 13441, "Anglerfish", 500, 1_900),
                SyncedItem("received", 995, "Coins", 950_000, 1),
            ),
        ),
        SyncedTrade(
            event_id="docs-player-trade",
            occurred_at=(now - timedelta(minutes=3)).isoformat(timespec="seconds"),
            event_type="player_trade",
            account_hash="docs-account",
            account_name="Example Player",
            counterparty="Friendly Trader",
            direction="exchange",
            metadata={},
            items=(
                SyncedItem("given", 385, "Shark", 10, 1_005),
                SyncedItem("received", 995, "Coins", 12_500, 1),
            ),
        ),
    )
    for trade in examples:
        window._journal.add_synced_trade(trade)


def add_pvm_example(window: MainWindow) -> None:
    """A loadout that's ready for mid-tier melee/ranged bosses but still missing the
    poison/Slayer requirements for Vorkath, Zulrah, and Cerberus — a realistic mix of
    Ready and Not ready rows rather than an all-green or all-empty snapshot."""
    equipment = (
        LoadoutItem(4_151, "Abyssal whip", 1, 1_500_000),
        LoadoutItem(12_926, "Toxic blowpipe", 1, 4_000_000),
        LoadoutItem(11_907, "Dragonfire shield", 1, 3_000_000),
    )
    inventory = (
        LoadoutItem(2_434, "Prayer potion(4)", 5, 12_000),
        LoadoutItem(12_899, "Trident of the swamp", 1, 1_200_000),
    )
    snapshot = LoadoutSnapshot(
        account_hash="docs-account",
        account_name="Example Player",
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        equipment=equipment,
        inventory=inventory,
        bank=(),
        skills={
            "Hitpoints": 99,
            "Ranged": 90,
            "Magic": 90,
            "Defence": 85,
            "Prayer": 77,
            "Strength": 90,
            "Slayer": 70,
        },
    )
    window._loadout_snapshot = snapshot
    window._render_pvm()


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "docs" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="osrs-toolkit-docs-"))

    # Isolate both locations used by journal migration. This guarantees that documentation
    # screenshots can never copy a developer's real journal or settings into the repository.
    os.environ["LOCALAPPDATA"] = str(profile_dir)
    os.environ["APPDATA"] = str(profile_dir)
    os.environ.setdefault("QT_SCALE_FACTOR", "1")

    app = QApplication([])
    app.setOrganizationName("OSRS Toolkit Screenshot")
    app.setApplicationName("OSRS Toolkit Screenshot")
    font_family = "Segoe UI"
    for font_path in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/seguisym.ttf"),
        Path("C:/Windows/Fonts/seguiemj.ttf"),
    ):
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                font_family = families[0]
    app.setFont(QFont(font_family, 10))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(profile_dir),
    )

    try:
        with patch("osrs_toolkit.app.QTimer.singleShot"):
            window = MainWindow()
        window._sync_timer.stop()
        window._market_timer.stop()
        window._sync_importer = RuneLiteSyncImporter(profile_dir / "runelite")
        window.resize(1_920, 1_000)
        window.show()

        client = WikiMarketClient()
        mappings, points = client.fetch_snapshot()
        window.cash.setValue(10_000_000)
        window.slots.setValue(8)
        window._market_loaded(mappings, points, client.used_cache)
        if not window._flips:
            raise RuntimeError("The current market snapshot produced no GE Flipper candidates")
        save_widget(window, output_dir / "ge-flipper.png", app)

        detail_candidate = window._flips[0]
        detail = ItemDetailsDialog(
            mappings[detail_candidate.item_id],
            next(point for point in points if point.item_id == detail_candidate.item_id),
            detail_candidate,
            True,
            client,
            window,
        )
        detail.resize(700, 720)
        detail.show()
        save_widget(detail, output_dir / "item-details.png", app)
        detail.close()

        window._watchlist = {candidate.item_id for candidate in window._flips[:10]}
        window._render_watchlist()
        show_page(window, "Watchlist")
        save_widget(window, output_dir / "watchlist.png", app)

        partial_id = add_journal_examples(window)
        add_supplies_examples(window)
        window._render_journal()
        show_page(window, "Trade Journal")
        show_journal_tab(window, "Plans && completed")
        # Seeded before this shot rather than for a tab of its own: the Grand Exchange
        # slots now sit on the Trade Journal page itself, so this is the screenshot that
        # has to show them filled rather than eight empty ones.
        add_ge_offer_state(window, window._sync_importer.sync_root)
        window._render_ge_offers()
        save_widget(window, output_dir / "trade-journal.png", app)

        partial_trade = next(
            trade
            for trade in window._journal.list_tracked()
            if trade.position_id == partial_id
        )
        fills = UpdateTrackedTradeDialog(partial_trade, window)
        fills.resize(720, 660)
        fills.show()
        save_widget(fills, output_dir / "trade-sale-fills.png", app)
        fills.close()

        show_journal_tab(window, "Supplies spend")
        save_widget(window, output_dir / "supplies-spend.png", app)

        add_buy_limit_example(window)
        window._render_buy_limits()
        show_journal_tab(window, "Buy limits")
        save_widget(window, output_dir / "buy-limits.png", app)

        add_performance_examples(window)
        add_savings_goal_example()
        window._render_journal()
        show_page(window, "Performance")
        window.performance_tabs.setCurrentIndex(0)
        save_widget(window, output_dir / "performance-strategy.png", app)
        window.performance_tabs.setCurrentIndex(1)
        save_widget(window, output_dir / "performance-plan.png", app)

        add_runelite_examples(window)
        profile = PlayerProfile(
            "Example Player",
            {
                "Overall": 1_608,
                "Magic": 82,
                "Cooking": 86,
                "Crafting": 72,
                "Fishing": 82,
                "Fletching": 75,
                "Herblore": 68,
                "Hunter": 65,
                "Smithing": 70,
                "Mining": 85,
                "Woodcutting": 90,
            },
        )
        window._account_loaded(profile)
        window.runelite_button.setText("RuneLite connected")
        window.runelite_status.setText(
            "RuneLite connected as Example Player • syncing automatically • player trades on"
        )
        window._render_synced_trades()
        # Selecting the tab is not enough. The sidebar still pointed at Performance from the
        # capture above, so this shot silently rendered the Performance page instead of the
        # journal's RuneLite activity tab.
        show_page(window, "Trade Journal")
        window.journal_tabs.setCurrentIndex(1)
        save_widget(window, output_dir / "runelite-activity.png", app)

        active_connection = RuneLiteConnectionStatus(
            detected=True,
            active=True,
            account_name="Example Player",
            account_hash="docs-account",
            player_trade_tracking=True,
        )
        with patch.object(
            window._sync_importer,
            "connection_status",
            return_value=active_connection,
        ):
            connection = RuneLiteConnectionDialog(window._sync_importer, window)
            connection.resize(760, 430)
            connection.show()
            save_widget(connection, output_dir / "runelite-connection.png", app)
            connection.close()

        window.alch_budget.setValue(5_000_000)
        show_page(window, "Alch Finder")
        save_widget(window, output_dir / "alch-finder.png", app)

        window.skill_profitable.setChecked(False)
        window.skill_available.setChecked(False)
        window._render_skilling()
        show_page(window, "Skilling Profit")
        save_widget(window, output_dir / "skilling-profit.png", app)

        add_pvm_example(window)
        show_page(window, "PvM Readiness")
        save_widget(window, output_dir / "pvm-readiness.png", app)

        settings = SettingsDialog("Dark", window._journal.database_path, window)
        settings.resize(760, 760)
        tabs = settings.findChild(QTabWidget)
        if tabs is not None:
            about_index = next(
                (i for i in range(tabs.count()) if tabs.tabText(i) == "About"), 1
            )
            tabs.setCurrentIndex(about_index)
        settings.update_status.setText(f"You are up to date — version {__version__}.")
        settings.update_button.setText("Check again")
        settings.show()
        save_widget(settings, output_dir / "settings-about.png", app)
        settings.close()
        window.close()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
