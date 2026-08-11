from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
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
from osrs_toolkit.journal import SyncedItem, SyncedTrade
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.runelite_sync import RuneLiteConnectionStatus, RuneLiteSyncImporter


def save_widget(widget: object, path: Path, app: QApplication) -> None:
    """Render a real Qt widget after its layout and paint events have settled."""
    app.processEvents()
    widget.repaint()  # type: ignore[attr-defined]
    app.processEvents()
    pixmap = widget.grab()  # type: ignore[attr-defined]
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


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
    return partial_id


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
            window,
        )
        detail.resize(700, 720)
        detail.show()
        save_widget(detail, output_dir / "item-details.png", app)
        detail.close()

        window._watchlist = {candidate.item_id for candidate in window._flips[:10]}
        window._render_watchlist()
        window.nav.setCurrentRow(1)
        save_widget(window, output_dir / "watchlist.png", app)

        partial_id = add_journal_examples(window)
        window._render_journal()
        window.nav.setCurrentRow(2)
        window.journal_tabs.setCurrentIndex(0)
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
        window.nav.setCurrentRow(3)
        save_widget(window, output_dir / "alch-finder.png", app)

        window.skill_profitable.setChecked(False)
        window.skill_available.setChecked(False)
        window._render_skilling()
        window.nav.setCurrentRow(4)
        save_widget(window, output_dir / "skilling-profit.png", app)

        window.nav.setCurrentRow(5)
        save_widget(window, output_dir / "kingdom-optimizer.png", app)

        settings = SettingsDialog("Dark", window)
        settings.resize(760, 760)
        tabs = settings.findChild(QTabWidget)
        if tabs is not None:
            tabs.setCurrentIndex(1)
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
