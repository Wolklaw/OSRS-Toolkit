from __future__ import annotations

import ctypes
import sys
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from osrs_toolkit import __version__
from osrs_toolkit.account import AccountLookupError, PlayerProfile, fetch_player
from osrs_toolkit.calculators import alch_candidates, kingdom_results, skill_results
from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.journal import JournalRepository, TrackedTrade
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.ranking import STRATEGIES, ge_tax, offer_targets, rank_flips
from osrs_toolkit.updater import (
    ReleaseInfo,
    download_installer,
    fetch_latest_release,
    is_newer_version,
    launch_installer,
)


class MarketWorker(QObject):
    finished = Signal(object, object, object)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()

    def run(self) -> None:
        try:
            client = WikiMarketClient()
            mappings, points = client.fetch_snapshot()
            self.finished.emit(mappings, points, client.used_cache)
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to the GUI.
            self.failed.emit(str(exc))


class UpdateCheckWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.finished.emit(fetch_latest_release())
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to the GUI.
            self.failed.emit(str(exc))


class UpdateDownloadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, release: ReleaseInfo) -> None:
        super().__init__()
        self.release = release

    def run(self) -> None:
        try:
            path = download_installer(self.release, self.progress.emit)
            self.finished.emit(path)
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to the GUI.
            self.failed.emit(str(exc))


class AccountDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect character")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("OSRS character name"))
        self.name = QLineEdit(placeholderText="Enter your RuneScape name")
        self.name.returnPressed.connect(self.accept)
        layout.addWidget(self.name)
        note = QLabel(
            "This reads public OSRS hiscores only. Never enter your Jagex password into this toolkit."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    THEMES: ClassVar[list[str]] = ["Dark", "Midnight", "Light"]

    def __init__(self, current_theme: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 390)
        self._update_thread: QThread | None = None
        self._update_worker: QObject | None = None
        self._available_release: ReleaseInfo | None = None
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.addWidget(QLabel("Theme"))
        self.theme = QComboBox()
        self.theme.addItems(self.THEMES)
        self.theme.setCurrentText(current_theme)
        appearance_layout.addWidget(self.theme)
        theme_note = QLabel("Your preference is saved for future launches.", objectName="muted")
        appearance_layout.addWidget(theme_note)
        appearance_layout.addStretch()
        tabs.addTab(appearance, "Appearance")

        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_text = QLabel(
            "<h2>About OSRS Toolkit</h2>"
            f"<p><b>Version {__version__}</b></p>"
            "<p>OSRS Toolkit brings live Grand Exchange opportunities and useful profit "
            "calculators together in one easy-to-use desktop app.</p>"
            "<p>Use it to explore possible flips, find profitable High Alchemy items, compare "
            "skilling methods, and plan your Managing Miscellania workers.</p>"
            "<p><b>Prices</b><br>Recent Grand Exchange trades from the OSRS Wiki and RuneLite "
            "real-time price service.</p>"
            "<p><b>Character lookup</b><br>Public skill levels from the official Old School "
            "RuneScape hiscores. The app never asks for your password, bank PIN, or "
            "authenticator code.</p>"
            "<p>Market recommendations are estimates and may not fill at the displayed prices.</p>"
            "<p>OSRS Toolkit is an independent fan-made app and is not affiliated with or "
            "endorsed by Jagex. Old School RuneScape and RuneScape are trademarks of Jagex Limited.</p>"
        )
        about_text.setWordWrap(True)
        about_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about_layout.addWidget(about_text)
        self.update_status = QLabel("Check GitHub for a newer official release.", objectName="muted")
        self.update_status.setWordWrap(True)
        about_layout.addWidget(self.update_status)
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self._update_action)
        about_layout.addWidget(self.update_button)
        about_layout.addStretch()
        tabs.addTab(about, "About")

        support = QWidget()
        support_layout = QVBoxLayout(support)
        support_text = QLabel(
            "OSRS Toolkit is free to use. If it saves you time and you would like to support "
            "its development, you can leave an optional tip. Every feature remains available "
            "whether you tip or not."
        )
        support_text.setWordWrap(True)
        support_layout.addWidget(support_text)
        tip_button = QPushButton("Tip the developer (optional)")
        tip_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://paypal.me/wolklaw"))
        )
        support_layout.addWidget(tip_button)
        support_layout.addStretch()
        tabs.addTab(support, "Support")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_action(self) -> None:
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        if self._available_release is None:
            self._check_for_updates()
        else:
            self._download_update(self._available_release)

    def _check_for_updates(self) -> None:
        self.update_button.setEnabled(False)
        self.update_status.setText("Checking for updates…")
        thread = QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._update_check_finished)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._clear_update_worker)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _update_check_finished(self, release: ReleaseInfo) -> None:
        if is_newer_version(release.version, __version__):
            self._available_release = release
            self.update_status.setText(
                f"Version {release.version} is available. The installer will be downloaded "
                "from the official GitHub release and verified before it opens."
            )
            self.update_button.setText(f"Download and install {release.version}")
        else:
            self.update_status.setText(f"You are up to date — version {__version__}.")
            self.update_button.setText("Check again")
        self.update_button.setEnabled(True)

    def _download_update(self, release: ReleaseInfo) -> None:
        answer = QMessageBox.question(
            self,
            "Install update",
            f"Download and install OSRS Toolkit {release.version}?\n\n"
            "Save any work first. The app will close when the installer opens.\n\n"
            "If you use the portable edition, this creates a standard installed copy; your "
            "portable folder remains unchanged.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.update_button.setEnabled(False)
        self.update_status.setText("Downloading update…")
        thread = QThread(self)
        worker = UpdateDownloadWorker(release)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(
            lambda value: self.update_status.setText(f"Downloading update… {value}%")
        )
        worker.finished.connect(self._update_downloaded)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._clear_update_worker)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _update_downloaded(self, path: Path) -> None:
        try:
            launch_installer(path)
        except Exception as exc:  # noqa: BLE001 - present launch failures to the user.
            self._update_failed(str(exc))
            return
        QApplication.quit()

    def _update_failed(self, message: str) -> None:
        self.update_status.setText("Update check failed. You can try again in a moment.")
        self.update_button.setText("Try again")
        self.update_button.setEnabled(True)
        QMessageBox.warning(self, "Update unavailable", message)

    def _clear_update_worker(self) -> None:
        if self._update_thread is not None:
            self._update_thread.deleteLater()
        self._update_worker = None
        self._update_thread = None


class TradeEntryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add completed trade")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.item_name = QLineEdit(placeholderText="Item name")
        self.quantity = self._number_field(1, 2_000_000_000, 1)
        self.buy_price = self._number_field(0, 2_000_000_000, 0)
        self.sell_price = self._number_field(0, 2_000_000_000, 0)
        self.buy_price.setSuffix(" gp")
        self.sell_price.setSuffix(" gp")
        form.addRow("Item", self.item_name)
        form.addRow("Quantity", self.quantity)
        form.addRow("Actual buy price", self.buy_price)
        form.addRow("Actual sell price", self.sell_price)
        layout.addLayout(form)
        note = QLabel(
            "Enter a completed buy-and-sell cycle. GE tax and realized profit are calculated automatically.",
            objectName="muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _number_field(minimum: int, maximum: int, value: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        field.setGroupSeparatorShown(True)
        return field

    def _accept_if_valid(self) -> None:
        if not self.item_name.text().strip():
            QMessageBox.warning(self, "Missing item", "Enter the item name.")
            return
        self.accept()


class UpdateTrackedTradeDialog(QDialog):
    STATUSES: ClassVar[list[str]] = [
        "Pending buy", "Bought", "Listed for sale", "Completed", "Cancelled"
    ]

    def __init__(self, trade: TrackedTrade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Update {trade.item_name}")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.status = QComboBox()
        self.status.addItems(self.STATUSES)
        self.status.setCurrentText(trade.status)
        self.actual_buy = TradeEntryDialog._number_field(0, 2_000_000_000, trade.actual_buy or trade.target_buy)
        self.actual_sell = TradeEntryDialog._number_field(0, 2_000_000_000, trade.actual_sell or trade.target_sell)
        self.actual_buy.setSuffix(" gp")
        self.actual_sell.setSuffix(" gp")
        form.addRow("Status", self.status)
        form.addRow("Actual buy price", self.actual_buy)
        form.addRow("Actual sell price", self.actual_sell)
        layout.addLayout(form)
        preserved = QLabel(
            f"Original targets remain saved: buy {_gp(trade.target_buy)}, "
            f"sell {_gp(trade.target_sell)}, quantity {trade.quantity:,}.",
            objectName="muted",
        )
        preserved.setWordWrap(True)
        layout.addWidget(preserved)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SortableTableItem(QTableWidgetItem):
    """Display formatted text while sorting by its underlying numeric meaning."""

    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.sort_value = _table_sort_value(value)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortableTableItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class MainWindow(QMainWindow):
    NAV_ITEMS: ClassVar[list[str]] = [
        "GE Flipper",
        "Watchlist",
        "Trade Journal",
        "Alch Finder",
        "Skilling Profit",
        "Kingdom Optimizer",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OSRS Toolkit")
        self.resize(1280, 760)
        self.setMinimumSize(1050, 680)
        self._thread: QThread | None = None
        self._worker: MarketWorker | None = None
        self._profile: PlayerProfile | None = None
        self._mappings: dict[int, ItemMapping] = {}
        self._points: list[MarketPoint] = []
        self._flips: list[FlipCandidate] = []
        self._theme = str(QSettings().value("appearance/theme", "Dark"))
        saved_watchlist = QSettings().value("market/watchlist", [])
        if not isinstance(saved_watchlist, list):
            saved_watchlist = [saved_watchlist]
        self._watchlist = {int(item_id) for item_id in saved_watchlist if str(item_id).isdigit()}
        self._journal = JournalRepository()
        self._profit_color = "#70d6a1"
        self._build_ui()
        self._apply_theme(self._theme)
        QTimer.singleShot(250, self.load_market)

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        brand = QFrame(objectName="brandCard")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(10, 10, 10, 10)
        brand_icon = QLabel()
        brand_icon.setPixmap(QIcon(str(_resource_path("assets/osrs_toolkit.ico"))).pixmap(48, 48))
        brand_layout.addWidget(brand_icon)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
        brand_copy.addWidget(QLabel("OSRS Toolkit", objectName="brandTitle"))
        brand_copy.addWidget(QLabel("Market Companion", objectName="brandSubtitle"))
        brand_layout.addLayout(brand_copy, 1)
        side.addWidget(brand)
        self.nav = QListWidget(objectName="nav")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setUniformItemSizes(True)
        self.nav.setSpacing(2)
        self.nav.addItems(self.NAV_ITEMS)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._change_page)
        side.addWidget(self.nav, 1)
        settings_button = QPushButton("⚙  Settings", objectName="settingsButton")
        settings_button.clicked.connect(self.open_settings)
        side.addWidget(settings_button)
        version_label = QLabel(f"Version {__version__}", objectName="versionLabel")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(version_label)
        outer.addWidget(sidebar)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 20, 30, 24)
        top = QHBoxLayout()
        self.market_status = QLabel("Loading market prices…", objectName="status")
        self.account_label = QLabel("No character connected", objectName="muted")
        self.account_button = QPushButton("Connect character", objectName="secondary")
        self.account_button.clicked.connect(self.connect_character)
        top.addWidget(self.market_status)
        top.addStretch()
        top.addWidget(self.account_label)
        top.addWidget(self.account_button)
        layout.addLayout(top)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_flip_page())
        self.pages.addWidget(self._build_watchlist_page())
        self.pages.addWidget(self._build_journal_page())
        self.pages.addWidget(self._build_alch_page())
        self.pages.addWidget(self._build_skilling_page())
        self.pages.addWidget(self._build_kingdom_page())
        layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)

    def _page_heading(self, title: str, subtitle: str) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.addWidget(QLabel(title, objectName="title"))
        label = QLabel(subtitle, objectName="muted")
        label.setWordWrap(True)
        layout.addWidget(label)
        return layout

    def _build_flip_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "GE Flipper",
            "Find possible margins using recent trades, volume, Grand Exchange tax, and risk checks. Double-click an item for details.",
        )
        controls = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search items…")
        self.search.textChanged.connect(self._render_flips)
        self.cash = QSpinBox()
        self.cash.setRange(10_000, 2_000_000_000)
        self.cash.setValue(10_000_000)
        self.cash.setSingleStep(1_000_000)
        self.cash.setPrefix("Cash stack  ")
        self.cash.setGroupSeparatorShown(True)
        self.cash.valueChanged.connect(self._cash_changed)
        self.strategy = QComboBox()
        self.strategy.addItems(list(STRATEGIES))
        self.strategy.setCurrentText("Balanced (1–4h)")
        self.strategy.currentTextChanged.connect(self._strategy_changed)
        self.refresh = QPushButton("Update prices")
        self.refresh.clicked.connect(self.load_market)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.cash)
        controls.addWidget(self.strategy)
        controls.addWidget(self.refresh)
        layout.addLayout(controls)
        self.flip_recommendation = QLabel("Waiting for market prices…", objectName="recommendation")
        self.flip_recommendation.setWordWrap(True)
        self.flip_recommendation.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.flip_recommendation.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout.addWidget(self.flip_recommendation)
        self.track_top_button = QPushButton("Track top recommendation")
        self.track_top_button.setEnabled(False)
        self.track_top_button.clicked.connect(self._track_top_recommendation)
        layout.addWidget(self.track_top_button)
        self.flip_table = self._table(
            ["Item", "Buy", "Sell", "Qty", "Profit ea.", "ROI", "1h volume", "Limit", "Potential", "Confidence"]
        )
        self.flip_table.cellDoubleClicked.connect(self._open_table_item)
        layout.addWidget(self.flip_table, 1)
        page.setLayout(layout)
        return page

    def _build_watchlist_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Watchlist",
            "Keep an eye on saved items and open any row for a full market breakdown.",
        )
        self.watchlist_empty = QLabel(
            "Your watchlist is empty. Double-click an item in GE Flipper or Alch Finder to add it.",
            objectName="status",
        )
        layout.addWidget(self.watchlist_empty)
        self.watchlist_table = self._table(
            ["Item", "Buy", "Sell", "Net margin", "ROI", "1h volume", "Price age"]
        )
        self.watchlist_table.cellDoubleClicked.connect(self._open_table_item)
        layout.addWidget(self.watchlist_table, 1)
        page.setLayout(layout)
        return page

    def _build_journal_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Trade Journal",
            "Record completed trades and measure your actual results after Grand Exchange tax.",
        )
        summary = QHBoxLayout()
        self.journal_profit = QLabel("Realized profit\n0 gp", objectName="summaryCard")
        self.journal_win_rate = QLabel("Win rate\n—", objectName="summaryCard")
        self.journal_invested = QLabel("Capital traded\n0 gp", objectName="summaryCard")
        summary.addWidget(self.journal_profit)
        summary.addWidget(self.journal_win_rate)
        summary.addWidget(self.journal_invested)
        layout.addLayout(summary)
        actions = QHBoxLayout()
        add_button = QPushButton("Add completed trade")
        add_button.clicked.connect(self._add_trade)
        update_button = QPushButton("Update selected trade", objectName="secondary")
        update_button.clicked.connect(self._update_selected_trade)
        delete_button = QPushButton("Delete selected", objectName="secondary")
        delete_button.clicked.connect(self._delete_selected_trade)
        actions.addWidget(add_button)
        actions.addWidget(update_button)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.journal_table = self._table(
            ["Date", "Status", "Item", "Qty", "Buy target", "Actual buy", "Sell target", "Actual sell", "P/L"]
        )
        layout.addWidget(self.journal_table, 1)
        page.setLayout(layout)
        self._render_journal()
        return page

    def _build_alch_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Alch Finder",
            "Find High Alchemy candidates after the item price and one nature rune.",
        )
        self.alch_table = self._table(["Item", "Buy price", "High alch", "Nature rune", "Profit/alch", "1h volume"])
        self.alch_table.cellDoubleClicked.connect(self._open_table_item)
        layout.addWidget(self.alch_table, 1)
        page.setLayout(layout)
        return page

    def _build_skilling_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Skilling Profit",
            "Compare processing methods using current prices. Connect a character to check skill requirements.",
        )
        self.skill_note = QLabel("Showing all levels until a character is connected.", objectName="status")
        layout.addWidget(self.skill_note)
        self.skill_table = self._table(["Method", "Skill", "Required", "Profit/action", "Estimated GP/hr", "Available"])
        layout.addWidget(self.skill_table, 1)
        page.setLayout(layout)
        return page

    def _build_kingdom_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Kingdom Optimizer",
            "Compare Royal Trouble worker assignments at 100% approval and full funding. Random secondary loot is excluded.",
        )
        self.kingdom_recommendation = QLabel("Waiting for market prices…", objectName="recommendation")
        self.kingdom_recommendation.setWordWrap(True)
        layout.addWidget(self.kingdom_recommendation)
        self.kingdom_table = self._table(["Resource", "Value/worker/day", "Cost/worker/day", "Profit/worker/day"])
        layout.addWidget(self.kingdom_table, 1)
        page.setLayout(layout)
        return page

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return table

    def _change_page(self, row: int) -> None:
        if row >= 0:
            self.pages.setCurrentIndex(row)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._theme, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._theme = dialog.theme.currentText()
        QSettings().setValue("appearance/theme", self._theme)
        self._apply_theme(self._theme)

    def connect_character(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._profile = fetch_player(dialog.name.text())
        except AccountLookupError as exc:
            QMessageBox.warning(self, "Could not connect character", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.account_label.setText(f"{self._profile.name} • Total {self._profile.total_level:,}")
        self.account_button.setText("Change character")
        self._render_skilling()

    def load_market(self) -> None:
        if self._thread is not None:
            return
        self.refresh.setEnabled(False)
        self.market_status.setText("Loading live market…")
        self._thread = QThread(self)
        self._worker = MarketWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._market_loaded)
        self._worker.failed.connect(self._market_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._worker_stopped)
        self._thread.start()

    def _market_loaded(
        self,
        mappings: dict[int, ItemMapping],
        points: list[MarketPoint],
        used_cache: bool,
    ) -> None:
        self._mappings, self._points = mappings, points
        # The cash field may have changed while the download was in progress.
        self._flips = rank_flips(
            mappings,
            points,
            cash_stack=self.cash.value(),
            strategy=self.strategy.currentText(),
        )
        self.refresh.setEnabled(True)
        source = "Cached prices" if used_cache else "Prices updated"
        self.market_status.setText(f"{source} • {datetime.now().astimezone():%H:%M:%S}")
        self._render_flips()
        self._render_alch()
        self._render_watchlist()
        self._render_journal()
        self._render_skilling()
        self._render_kingdom()

    def _cash_changed(self, cash_stack: int) -> None:
        """Re-rank the local snapshot immediately; no market download is needed."""
        if not self._points:
            return
        self._flips = rank_flips(
            self._mappings,
            self._points,
            cash_stack=cash_stack,
            strategy=self.strategy.currentText(),
        )
        self._render_flips()

    def _strategy_changed(self, strategy: str) -> None:
        if not self._points:
            return
        self._flips = rank_flips(
            self._mappings,
            self._points,
            cash_stack=self.cash.value(),
            strategy=strategy,
        )
        self._render_flips()

    def _market_failed(self, message: str) -> None:
        self.refresh.setEnabled(True)
        self.market_status.setText(f"Market unavailable • {message}")

    def _worker_stopped(self) -> None:
        self._worker = None
        self._thread = None

    def _render_flips(self) -> None:
        query = self.search.text().strip().casefold()
        rows = [item for item in self._flips if query in item.name.casefold()][:250]
        values = [[item.name, _gp(item.buy_price), _gp(item.sell_price), f"{item.suggested_quantity:,}", _gp(item.profit_each), f"{item.roi:.2f}%", f"{item.hourly_volume:,}", f"{item.buy_limit:,}" if item.buy_limit else "—", _gp(item.potential_profit), f"{item.confidence}%"] for item in rows]
        self._fill_table(
            self.flip_table,
            values,
            green_columns={4, 5},
            row_ids=[item.item_id for item in rows],
        )
        if self._flips:
            self.track_top_button.setEnabled(True)
            mode = self.strategy.currentText()
            plans = [f"Our recommendations — {mode}"]
            for number, item in enumerate(self._flips[:3], start=1):
                plans.append(
                    f"{number}. BUY {item.suggested_quantity:,} × {item.name} at "
                    f"{_gp(item.buy_price)} each ({_gp(item.capital_required)} total); "
                    f"THEN SELL at {_gp(item.sell_price)} each after the buy fills. "
                    f"Estimate: {_gp(item.potential_profit)} after tax; confidence {item.confidence}%."
                )
            plans.append(
                "Buy and sell targets are patient limit offers anchored to recent five-minute "
                "trades. They are not instant prices, and neither offer is guaranteed to fill."
            )
            self.flip_recommendation.setText("\n".join(plans))
        else:
            self.track_top_button.setEnabled(False)
            self.flip_recommendation.setText(
                "No opportunity currently passes this mode’s affordability, liquidity, stability, and confidence rules."
            )

    def _render_alch(self) -> None:
        rows = alch_candidates(self._mappings, self._points)[:300]
        values = [[row.name, _gp(row.buy_price), _gp(row.alch_value), _gp(row.rune_cost), _gp(row.profit), f"{row.volume:,}"] for row in rows]
        self._fill_table(
            self.alch_table,
            values,
            green_columns={4},
            row_ids=[row.item_id for row in rows],
        )

    def _render_watchlist(self) -> None:
        point_by_id = {point.item_id: point for point in self._points}
        rows: list[list[str]] = []
        row_ids: list[int] = []
        now = int(datetime.now().astimezone().timestamp())
        for item_id in sorted(
            self._watchlist,
            key=lambda saved_id: self._mappings.get(saved_id, ItemMapping(saved_id, "", False, None, None)).name,
        ):
            item, point = self._mappings.get(item_id), point_by_id.get(item_id)
            if item is None or point is None:
                continue
            buy_price, sell_price = offer_targets(point)
            profit = sell_price - buy_price - ge_tax(sell_price)
            roi = profit / buy_price * 100 if buy_price else 0
            age = max(0, now - min(point.high_time, point.low_time))
            rows.append([
                item.name,
                _gp(buy_price),
                _gp(sell_price),
                _gp(profit),
                f"{roi:.2f}%",
                f"{point.volume_1h:,}",
                f"{age // 60} min",
            ])
            row_ids.append(item_id)
        self.watchlist_empty.setVisible(not rows)
        self._fill_table(self.watchlist_table, rows, green_columns={3, 4}, row_ids=row_ids)

    def _render_journal(self) -> None:
        tracked = self._journal.list_tracked()
        trades = self._journal.list_all()
        values = [
            [
                trade.created_at[:10],
                trade.status,
                trade.item_name,
                f"{trade.quantity:,}",
                _gp(trade.target_buy),
                _gp(trade.actual_buy) if trade.actual_buy is not None else "—",
                _gp(trade.target_sell),
                _gp(trade.actual_sell) if trade.actual_sell is not None else "—",
                _gp(trade.realized_profit)
                if trade.realized_profit is not None
                else f"Est. {_gp(trade.estimated_profit)}",
            ]
            for trade in tracked
        ]
        values.extend([
            [
                trade.recorded_at[:10],
                "Completed (manual)",
                trade.item_name,
                f"{trade.quantity:,}",
                _gp(trade.buy_price),
                _gp(trade.buy_price),
                _gp(trade.sell_price),
                _gp(trade.sell_price),
                _gp(trade.profit),
            ]
            for trade in trades
        ])
        self._fill_table(
            self.journal_table,
            values,
            green_columns={8},
            row_ids=[trade.position_id for trade in tracked] + [trade.trade_id for trade in trades],
        )
        completed_positions = [trade for trade in tracked if trade.realized_profit is not None]
        profits = [trade.profit for trade in trades] + [
            trade.realized_profit or 0 for trade in completed_positions
        ]
        total_profit = sum(profits)
        total_invested = sum(trade.invested for trade in trades) + sum(
            trade.invested for trade in completed_positions
        )
        wins = sum(profit > 0 for profit in profits)
        win_rate = wins / len(profits) * 100 if profits else 0
        self.journal_profit.setText(f"Realized profit\n{_gp(total_profit)}")
        self.journal_win_rate.setText(
            f"Win rate\n{win_rate:.1f}%" if profits else "Win rate\n—"
        )
        self.journal_invested.setText(f"Capital traded\n{_gp(total_invested)}")

    def _add_trade(self) -> None:
        dialog = TradeEntryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._journal.add(
            dialog.item_name.text(),
            dialog.quantity.value(),
            dialog.buy_price.value(),
            dialog.sell_price.value(),
        )
        self._render_journal()

    def _track_top_recommendation(self) -> None:
        if self._flips:
            self._track_candidate(self._flips[0])

    def _track_candidate(self, candidate: FlipCandidate) -> None:
        self._journal.track(
            candidate.item_id,
            candidate.name,
            candidate.suggested_quantity,
            candidate.buy_price,
            candidate.sell_price,
        )
        self._render_journal()
        self.nav.setCurrentRow(2)

    def _delete_selected_trade(self) -> None:
        row = self.journal_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No trade selected", "Select a journal row first.")
            return
        record_id = self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        status = self.journal_table.item(row, 1).text()
        if not isinstance(record_id, int):
            return
        answer = QMessageBox.question(
            self,
            "Delete trade",
            "Remove this trade from your journal?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if status in UpdateTrackedTradeDialog.STATUSES:
                self._journal.delete_tracked(record_id)
            else:
                self._journal.delete(record_id)
            self._render_journal()

    def _update_selected_trade(self) -> None:
        row = self.journal_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No trade selected", "Select a tracked trade first.")
            return
        position_id = self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        status = self.journal_table.item(row, 1).text()
        if not isinstance(position_id, int) or status not in UpdateTrackedTradeDialog.STATUSES:
            QMessageBox.information(
                self, "Completed manual entry", "Manual completed entries cannot change status."
            )
            return
        trade = next(
            (entry for entry in self._journal.list_tracked() if entry.position_id == position_id),
            None,
        )
        if trade is None:
            return
        dialog = UpdateTrackedTradeDialog(trade, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_status = dialog.status.currentText()
        actual_buy = dialog.actual_buy.value() if new_status in {"Bought", "Listed for sale", "Completed"} else None
        actual_sell = dialog.actual_sell.value() if new_status == "Completed" else None
        self._journal.update_tracked(position_id, new_status, actual_buy, actual_sell)
        self._render_journal()

    def _render_skilling(self) -> None:
        skills = self._profile.skills if self._profile else {}
        rows = skill_results(self._mappings, self._points, skills)
        if self._profile:
            self.skill_note.setText(f"Requirements checked against {self._profile.name}'s public hiscores.")
        values = [[row.name, row.skill, str(row.level), _gp(row.profit_action), _gp(row.profit_hour), "Yes" if row.eligible else "No"] for row in rows]
        self._fill_table(self.skill_table, values, green_columns={4})

    def _render_kingdom(self) -> None:
        rows = kingdom_results(self._mappings, self._points)
        values = [[row.resource, _gp(row.value_worker), _gp(row.cost_worker), _gp(row.profit_worker)] for row in rows]
        self._fill_table(self.kingdom_table, values, green_columns={3})
        if len(rows) >= 2:
            self.kingdom_recommendation.setText(
                f"Recommended: 10 workers on {rows[0].resource} and 5 on {rows[1].resource}. "
                f"Estimated primary-output profit: {_gp(rows[0].profit_worker * 10 + rows[1].profit_worker * 5)} per day."
            )

    def _fill_table(
        self,
        table: QTableWidget,
        values: list[list[str]],
        green_columns: set[int],
        row_ids: list[int] | None = None,
    ) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(values))
        for row_index, row in enumerate(values):
            for column, value in enumerate(row):
                cell = SortableTableItem(value)
                if column == 0 and row_ids is not None:
                    cell.setData(Qt.ItemDataRole.UserRole, row_ids[row_index])
                if column > 0:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column in green_columns:
                    cell.setForeground(QColor(self._profit_color))
                table.setItem(row_index, column, cell)
        table.resizeColumnsToContents()
        table.setSortingEnabled(True)

    def _open_table_item(self, row: int, _column: int) -> None:
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return
        item_id = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(item_id, int):
            return
        mapping = self._mappings.get(item_id)
        point = next((candidate for candidate in self._points if candidate.item_id == item_id), None)
        if mapping is None or point is None:
            return
        flip = next((candidate for candidate in self._flips if candidate.item_id == item_id), None)
        dialog = ItemDetailsDialog(mapping, point, flip, item_id in self._watchlist, self)
        dialog.exec()
        if dialog.track_requested and flip is not None:
            self._track_candidate(flip)
        if dialog.watched:
            self._watchlist.add(item_id)
        else:
            self._watchlist.discard(item_id)
        QSettings().setValue("market/watchlist", sorted(self._watchlist))
        self._render_watchlist()

    def _apply_theme(self, theme: str) -> None:
        palettes = {
            "Dark": ("#11151b", "#171c24", "#e8edf3", "#91a0b4", "#1a2029", "#28313d", "#1e252f", "#70d6a1"),
            "Midnight": ("#08111f", "#0d192b", "#e5efff", "#8fa5c4", "#122139", "#263b59", "#172942", "#67d7c4"),
            "Light": ("#f3f5f7", "#e7ebef", "#1d2833", "#5e6b78", "#ffffff", "#cbd3dc", "#dfe5eb", "#16845b"),
        }
        background, sidebar, text, muted, field, border, header, profit = palettes.get(
            theme, palettes["Dark"]
        )
        self._profit_color = profit
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {background}; color: {text}; font-size: 13px; }}
            #sidebar {{ background: {sidebar}; border-right: 1px solid {border}; }}
            #brandCard {{ background: {field}; border: 1px solid {border}; border-radius: 10px; }}
            #brandTitle {{ color: {text}; font-size: 16px; font-weight: 700; }}
            #brandSubtitle, #versionLabel {{ color: {muted}; font-size: 11px; }}
            #title {{ font-size: 28px; font-weight: 650; margin-top: 12px; }}
            #muted, #status {{ color: {muted}; }}
            #recommendation {{ background: {field}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 14px; }}
            #summaryCard {{ background: {field}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 14px; font-size: 14px; }}
            #nav {{ background: transparent; border: 0; outline: 0; padding-top: 16px; }}
            #nav::item {{ padding: 11px 12px; border-radius: 7px; margin: 1px 2px; }}
            #nav::item:selected {{ background: {header}; color: #d5ad52; }}
            QLineEdit, QSpinBox, QComboBox {{ background: {field}; border: 1px solid {border}; border-radius: 7px; padding: 9px; }}
            QPushButton {{ background: #d5ad52; color: #11151b; border: 0; border-radius: 7px; padding: 10px 18px; font-weight: 650; }}
            QPushButton#secondary, QPushButton#settingsButton {{ background: {header}; color: {text}; border: 1px solid {border}; }}
            QPushButton:disabled {{ background: #645735; color: #aaa; }}
            QTableWidget {{ background: {background}; alternate-background-color: {field}; border: 1px solid {border}; border-radius: 8px; gridline-color: {border}; }}
            QHeaderView::section {{ background: {header}; color: {text}; border: 0; border-bottom: 1px solid {border}; padding: 10px; }}
            QTableWidget::item {{ padding: 7px; }}
        """)
        self._render_flips()
        self._render_alch()
        self._render_watchlist()
        self._render_journal()
        self._render_skilling()
        self._render_kingdom()


def _gp(value: int) -> str:
    return f"{value:,} gp"


def _table_sort_value(value: str) -> tuple[int, float | str]:
    cleaned = value.strip()
    if cleaned == "—":
        return (0, -1.0)
    numeric = cleaned.removesuffix(" gp").removesuffix("%").replace(",", "")
    try:
        return (0, float(numeric))
    except ValueError:
        return (1, cleaned.casefold())


def _resource_path(relative_path: str) -> Path:
    """Find bundled resources in releases and project resources during development."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundled_root) if bundled_root else Path(__file__).resolve().parents[2]
    return root / relative_path


def _set_windows_app_id() -> None:
    """Give Windows a stable taskbar identity instead of Python's generic identity."""
    if sys.platform != "win32":
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
        "OSRSToolkit.Desktop.1.1"
    )


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setOrganizationName("OSRS Toolkit")
    app.setApplicationName("OSRS Toolkit")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(QIcon(str(_resource_path("assets/osrs_toolkit.ico"))))
    window = MainWindow()
    window.show()
    return app.exec()
