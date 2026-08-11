from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QTabWidget

from osrs_toolkit.account import PlayerProfile
from osrs_toolkit.app import MainWindow, SettingsDialog
from osrs_toolkit.journal import SyncedItem, SyncedTrade
from osrs_toolkit.market import WikiMarketClient


def save_widget(widget: object, path: Path, app: QApplication) -> None:
    app.processEvents()
    pixmap = widget.grab()  # type: ignore[attr-defined]
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "docs" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="osrs-toolkit-docs-"))
    os.environ["LOCALAPPDATA"] = str(profile_dir)

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
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(profile_dir))

    try:
        with patch("osrs_toolkit.app.QTimer.singleShot"):
            window = MainWindow()
        window._sync_timer.stop()
        window.resize(1440, 900)
        window.show()
        mappings, points = WikiMarketClient().fetch_snapshot()
        window.cash.setValue(2_000_000)
        window.slots.setValue(6)
        window._market_loaded(mappings, points, False)
        save_widget(window, output_dir / "ge-flipper.png", app)

        first = window._flips[0]
        position_id = window._journal.track(
            first.item_id,
            first.name,
            first.suggested_quantity,
            first.buy_price,
            first.sell_price,
        )
        window._journal.update_tracked(
            position_id,
            "Listed for sale",
            first.buy_price,
            None,
        )
        window._journal.add("Rune platebody", 12, 37_500, 39_250)
        window._render_journal()
        window.nav.setCurrentRow(2)
        window.journal_tabs.setCurrentIndex(0)
        save_widget(window, output_dir / "trade-journal.png", app)

        window._journal.add_synced_trade(
            SyncedTrade(
                event_id="docs-ge-fill",
                occurred_at="2026-08-11T20:14:32+00:00",
                event_type="ge_fill",
                account_hash="docs-account",
                account_name="Example Player",
                counterparty=None,
                direction="buy",
                metadata={"offer_price": 2320},
                items=(
                    SyncedItem("given", 995, "Coins", 2250, 1),
                    SyncedItem("received", 13441, "Anglerfish", 1, 2250),
                ),
            )
        )
        window._journal.add_synced_trade(
            SyncedTrade(
                event_id="docs-player-trade",
                occurred_at="2026-08-11T20:18:07+00:00",
                event_type="player_trade",
                account_hash="docs-account",
                account_name="Example Player",
                counterparty="Friendly Trader",
                direction="exchange",
                metadata={},
                items=(
                    SyncedItem("given", 385, "Shark", 10, 1005),
                    SyncedItem("received", 995, "Coins", 12500, 1),
                ),
            )
        )
        window.runelite_button.setText("RuneLite connected")
        window.account_label.setText("Example Player • Total 1,608")
        window.account_button.setText("Change character")
        window.runelite_status.setText(
            "RuneLite connected as Example Player • syncing automatically • player trades on"
        )
        window._profile = PlayerProfile(
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
        window.skill_available.setEnabled(True)
        window._render_alch()
        window._render_skilling()
        window._render_synced_trades()
        window.journal_tabs.setCurrentIndex(1)
        save_widget(window, output_dir / "runelite-activity.png", app)

        window.nav.setCurrentRow(3)
        save_widget(window, output_dir / "alch-finder.png", app)

        window.nav.setCurrentRow(4)
        save_widget(window, output_dir / "skilling-profit.png", app)

        settings = SettingsDialog("Dark", window)
        settings.resize(680, 680)
        tabs = settings.findChild(QTabWidget)
        if tabs is not None:
            tabs.setCurrentIndex(1)
        settings.show()
        save_widget(settings, output_dir / "updates.png", app)
        settings.close()
        window.close()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
