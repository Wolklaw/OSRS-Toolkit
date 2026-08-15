from __future__ import annotations

import ctypes
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
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
from osrs_toolkit.calculators import (
    ALCH_POLICIES,
    SKILL_METHODS,
    alch_candidates,
    kingdom_results,
    skill_results,
)
from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade, TrackedTrade
from osrs_toolkit.journal_presentation import (
    JOURNAL_STATUS_FILTERS,
    PERIOD_FILTERS,
    JournalPLPresentation,
    journal_pl_presentation,
    journal_status_matches,
    trade_within_period,
)
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.ranking import (
    STRATEGIES,
    ge_tax,
    offer_targets,
    plan_flip_portfolio,
    rank_flips,
)
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter
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


class AccountWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, character_name: str) -> None:
        super().__init__()
        self.character_name = character_name

    def run(self) -> None:
        try:
            self.finished.emit(fetch_player(self.character_name))
        except AccountLookupError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to the GUI.
            self.failed.emit(f"Unexpected error while looking up hiscores: {exc}")


class SearchLineEdit(QLineEdit):
    """A native-feeling search field with a clear action and Escape support."""

    def __init__(self, placeholder: str) -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            return
        super().keyPressEvent(event)


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


def _default_database_path() -> Path:
    local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
    return local_data / "OSRSToolkit" / "data" / "toolkit.db"


class SettingsDialog(QDialog):
    THEMES: ClassVar[list[str]] = ["Dark", "Midnight", "Light"]

    def __init__(
        self,
        current_theme: str,
        database_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 390)
        self._update_thread: QThread | None = None
        self._update_worker: QObject | None = None
        self._available_release: ReleaseInfo | None = None
        self.requested_database_path: Path | None = None
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

        data = QWidget()
        data_layout = QVBoxLayout(data)
        data_layout.addWidget(QLabel("Database location"))
        self._current_database_path = database_path
        self.database_path_field = QLineEdit(str(self._current_database_path))
        self.database_path_field.setReadOnly(True)
        data_layout.addWidget(self.database_path_field)
        data_note = QLabel(
            "Your trade journal, tracked positions, and RuneLite activity are stored in this "
            "SQLite file. Moving it copies your existing data to the new location; the old "
            "file is left in place untouched.",
            objectName="muted",
        )
        data_note.setWordWrap(True)
        data_layout.addWidget(data_note)
        data_actions = QHBoxLayout()
        change_location_button = QPushButton("Change location…", objectName="secondary")
        change_location_button.clicked.connect(self._choose_database_location)
        default_location_button = QPushButton("Use default location", objectName="secondary")
        default_location_button.clicked.connect(self._use_default_database_location)
        data_actions.addWidget(change_location_button)
        data_actions.addWidget(default_location_button)
        data_actions.addStretch()
        data_layout.addLayout(data_actions)
        data_layout.addStretch()
        tabs.addTab(data, "Data")

        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_text = QLabel(
            "<h2>About OSRS Toolkit</h2>"
            f"<p><b>Version {__version__}</b></p>"
            "<p>An independent, fan-made market companion with Grand Exchange research, "
            "profit calculators, and a local trade journal.</p>"
            "<p><b>Game interaction</b><br>This toolkit does not play Old School RuneScape, "
            "generate game input, communicate with game worlds, alter network traffic, or "
            "modify the game client. Optional RuneLite sync only imports local trade events.</p>"
            "<p><b>Data and privacy</b><br>Prices come from the OSRS Wiki real-time price API. "
            "Character lookup reads public hiscores. Never enter a Jagex password, bank PIN, "
            "or authenticator code. Journal data stays on this computer in a version-independent "
            "user-data folder with automatic recovery backups.</p>"
            "<p><b>Unofficial fan project</b><br>OSRS Toolkit is not affiliated with, sponsored "
            "by, or endorsed by Jagex. Jagex, RuneScape, and Old School RuneScape are trademarks "
            "of Jagex Limited. All game-related intellectual property belongs to Jagex and its "
            "licensors.</p>"
            "<p><b>Created using intellectual property belonging to Jagex Limited under the "
            "terms of Jagex's Fan Content Policy.</b></p>"
            "<p><a href='https://legal.jagex.com/docs/policies/fan-content-policy'>Fan Content Policy</a>"
            " &nbsp;&bull;&nbsp; <a href='https://legal.jagex.com/docs/rules/rules-of-old-school-runescape'>"
            "Rules of Old School RuneScape</a> &nbsp;&bull;&nbsp; "
            "<a href='https://legal.jagex.com/docs/terms/terms-and-conditions/current'>Terms</a></p>"
            "<p>Market results are estimates, not guarantees, and may not fill at displayed prices.</p>"
        )
        about_text.setWordWrap(True)
        about_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        about_text.setOpenExternalLinks(True)
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

    def _choose_database_location(self) -> None:
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Choose database location",
            str(self._current_database_path),
            "SQLite Database (*.db)",
        )
        if not chosen:
            return
        self._set_requested_database_path(Path(chosen))

    def _use_default_database_location(self) -> None:
        self._set_requested_database_path(_default_database_path())

    def _set_requested_database_path(self, path: Path) -> None:
        if path == self._current_database_path:
            return
        self.requested_database_path = path
        self.database_path_field.setText(str(path))

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


class RuneLiteConnectionDialog(QDialog):
    PLUGIN_URL = "https://github.com/Wolklaw/osrs-toolkit-runelite"

    def __init__(self, importer: RuneLiteSyncImporter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.importer = importer
        self.setWindowTitle("Connect RuneLite")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>Connect RuneLite</h2>"))
        explanation = QLabel(
            "Install and enable the OSRS Toolkit plugin in RuneLite. It records Grand Exchange "
            "fills locally while this app is closed and imports them automatically when the app "
            "opens. Player-to-player trade tracking is optional in the RuneLite plugin settings."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.status = QLabel(objectName="recommendation")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        privacy = QLabel(
            "The bridge uses local files only. No Jagex credentials or trade history are sent "
            "to OSRS Toolkit servers.",
            objectName="muted",
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        actions = QHBoxLayout()
        plugin_button = QPushButton("View RuneLite plugin")
        plugin_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.PLUGIN_URL))
        )
        self.folder_button = QPushButton("Open sync folder", objectName="secondary")
        self.folder_button.clicked.connect(self._open_folder)
        check_button = QPushButton("Check connection", objectName="secondary")
        check_button.clicked.connect(self.refresh_status)
        actions.addWidget(plugin_button)
        actions.addWidget(self.folder_button)
        actions.addWidget(check_button)
        layout.addLayout(actions)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(self.reject)
        layout.addWidget(close_button)
        self.refresh_status()

    def refresh_status(self) -> None:
        connection = self.importer.connection_status()
        if connection.active:
            character = f" as {connection.account_name}" if connection.account_name else ""
            player_trades = "on" if connection.player_trade_tracking else "off"
            self.status.setText(
                f"Connected{character} — new trades will sync automatically. "
                f"Player-trade tracking is {player_trades}."
            )
        elif connection.detected:
            self.status.setText(
                "Plugin data found, but RuneLite is not currently active. Saved trades will still "
                "import when available."
            )
        else:
            self.status.setText(
                "Not connected yet. Install and enable the RuneLite plugin, then choose Check "
                "connection."
            )
        self.folder_button.setEnabled(connection.detected)

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.importer.sync_root)))


class SyncedTradeDetailsDialog(QDialog):
    def __init__(self, trade: SyncedTrade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trade details")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        heading = "Grand Exchange fill" if trade.event_type == "ge_fill" else "Player trade"
        layout.addWidget(QLabel(f"<h2>{heading}</h2>"))
        lines = [
            f"Time: {trade.occurred_at}",
            f"Character: {trade.account_name}",
        ]
        if trade.counterparty:
            lines.append(f"Other player: {trade.counterparty}")
        lines.extend(["", "Given:"])
        lines.extend(_item_detail_lines(trade.given))
        lines.extend(["", "Received:"])
        lines.extend(_item_detail_lines(trade.received))
        if trade.event_type == "player_trade":
            lines.extend(
                [
                    "",
                    f"Estimated given value: {_gp(trade.given_value)}",
                    f"Estimated received value: {_gp(trade.received_value)}",
                    f"Estimated difference: {_signed_gp(trade.estimated_difference)}",
                    "Item values are RuneLite guide-price estimates captured at trade time.",
                ]
            )
        details = QLabel("\n".join(lines))
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        details.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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
        "Pending buy", "Bought", "Listed for sale", "Partially sold", "Completed", "Cancelled"
    ]

    def __init__(self, trade: TrackedTrade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Update {trade.item_name}")
        self.setMinimumSize(560, 640)
        self.trade = trade
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.status = QComboBox()
        self.status.addItems(self.STATUSES)
        self.status.setCurrentText(trade.status)
        floor = max(1, trade.bought_quantity, trade.sold_quantity)
        self.quantity_acquired = TradeEntryDialog._number_field(
            floor, max(floor, trade.quantity), trade.quantity
        )
        self.quantity_acquired.setToolTip(
            "Reduce this if a buy order was cancelled early and only part of it filled — "
            "the position becomes the smaller amount you actually hold, so it can still be "
            "listed and sold."
        )
        self.quantity_acquired.valueChanged.connect(self._quantity_acquired_changed)
        form.addRow("Status", self.status)
        form.addRow("Quantity acquired", self.quantity_acquired)
        layout.addLayout(form)
        preserved = QLabel(
            f"Original targets remain saved: buy {_gp(trade.target_buy)}, "
            f"sell {_gp(trade.target_sell)}, quantity {trade.quantity:,}."
            f"\nStrategy: {trade.strategy}. Current suggestion: buy "
            f"{_gp(trade.buy_suggestion)}, sell {_gp(trade.sell_suggestion)}"
            + (
                f" (reviewed {trade.suggestion_reviewed_at[:10]})."
                if trade.suggestion_reviewed_at
                else "."
            ),
            objectName="muted",
        )
        preserved.setWordWrap(True)
        layout.addWidget(preserved)

        layout.addWidget(QLabel("Buy fills", objectName="sectionHeader"))
        self.buy_fills_table = QTableWidget(0, 2)
        self.buy_fills_table.setHorizontalHeaderLabels(["Quantity", "Buy price"])
        self.buy_fills_table.horizontalHeader().setStretchLastSection(True)
        self.buy_fills_table.verticalHeader().setVisible(False)
        self.buy_fills_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.buy_fills_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.buy_fills_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for buy_fill in trade.buy_fills:
            self._append_buy_fill(buy_fill.quantity, buy_fill.buy_price)
        layout.addWidget(self.buy_fills_table, 1)

        buy_fill_controls = QHBoxLayout()
        self.buy_fill_quantity = TradeEntryDialog._number_field(
            1, trade.quantity, max(1, trade.quantity - trade.bought_quantity)
        )
        self.buy_fill_price = TradeEntryDialog._number_field(1, 2_000_000_000, trade.target_buy)
        self.buy_fill_price.setSuffix(" gp")
        add_buy_fill = QPushButton("Add buy fill")
        add_buy_fill.clicked.connect(self._add_buy_fill)
        remove_buy_fill = QPushButton("Remove selected", objectName="secondary")
        remove_buy_fill.clicked.connect(self._remove_buy_fill)
        buy_fill_controls.addWidget(QLabel("Quantity"))
        buy_fill_controls.addWidget(self.buy_fill_quantity)
        buy_fill_controls.addWidget(QLabel("Price"))
        buy_fill_controls.addWidget(self.buy_fill_price)
        buy_fill_controls.addWidget(add_buy_fill)
        buy_fill_controls.addWidget(remove_buy_fill)
        layout.addLayout(buy_fill_controls)
        self.buy_fill_summary = QLabel(objectName="muted")
        layout.addWidget(self.buy_fill_summary)

        layout.addWidget(QLabel("Sale fills", objectName="sectionHeader"))
        self.sale_fills_table = QTableWidget(0, 2)
        self.sale_fills_table.setHorizontalHeaderLabels(["Quantity", "Sell price"])
        self.sale_fills_table.horizontalHeader().setStretchLastSection(True)
        self.sale_fills_table.verticalHeader().setVisible(False)
        self.sale_fills_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sale_fills_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sale_fills_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for sale_fill in trade.sale_fills:
            self._append_sale_fill(sale_fill.quantity, sale_fill.sell_price)
        layout.addWidget(self.sale_fills_table, 1)

        sale_fill_controls = QHBoxLayout()
        self.sale_fill_quantity = TradeEntryDialog._number_field(
            1, trade.quantity, trade.remaining_quantity or 1
        )
        self.sale_fill_price = TradeEntryDialog._number_field(1, 2_000_000_000, trade.target_sell)
        self.sale_fill_price.setSuffix(" gp")
        add_sale_fill = QPushButton("Add sale fill")
        add_sale_fill.clicked.connect(self._add_sale_fill)
        remove_sale_fill = QPushButton("Remove selected", objectName="secondary")
        remove_sale_fill.clicked.connect(self._remove_sale_fill)
        sale_fill_controls.addWidget(QLabel("Quantity"))
        sale_fill_controls.addWidget(self.sale_fill_quantity)
        sale_fill_controls.addWidget(QLabel("Price"))
        sale_fill_controls.addWidget(self.sale_fill_price)
        sale_fill_controls.addWidget(add_sale_fill)
        sale_fill_controls.addWidget(remove_sale_fill)
        layout.addLayout(sale_fill_controls)
        self.sale_fill_summary = QLabel(objectName="muted")
        layout.addWidget(self.sale_fill_summary)

        self._update_buy_fill_summary()
        self._update_sale_fill_summary()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _quantity_acquired_changed(self, _value: int) -> None:
        self._update_buy_fill_summary()
        self._update_sale_fill_summary()

    def _append_buy_fill(self, quantity: int, price: int) -> None:
        row = self.buy_fills_table.rowCount()
        self.buy_fills_table.insertRow(row)
        quantity_cell = QTableWidgetItem(f"{quantity:,}")
        quantity_cell.setData(Qt.ItemDataRole.UserRole, quantity)
        price_cell = QTableWidgetItem(_gp(price))
        price_cell.setData(Qt.ItemDataRole.UserRole, price)
        self.buy_fills_table.setItem(row, 0, quantity_cell)
        self.buy_fills_table.setItem(row, 1, price_cell)

    def buy_fills(self) -> list[tuple[int, int]]:
        return [
            (
                int(self.buy_fills_table.item(row, 0).data(Qt.ItemDataRole.UserRole)),
                int(self.buy_fills_table.item(row, 1).data(Qt.ItemDataRole.UserRole)),
            )
            for row in range(self.buy_fills_table.rowCount())
        ]

    def _add_buy_fill(self) -> None:
        quantity = self.buy_fill_quantity.value()
        existing = sum(fill_quantity for fill_quantity, _price in self.buy_fills())
        if existing + quantity > self.quantity_acquired.value():
            QMessageBox.warning(
                self,
                "Too many items",
                "Buy-fill quantities cannot exceed the acquired quantity.",
            )
            return
        self._append_buy_fill(quantity, self.buy_fill_price.value())
        self._update_buy_fill_summary()

    def _remove_buy_fill(self) -> None:
        row = self.buy_fills_table.currentRow()
        if row >= 0:
            self.buy_fills_table.removeRow(row)
            self._update_buy_fill_summary()

    def _update_buy_fill_summary(self) -> None:
        fills = self.buy_fills()
        bought = sum(quantity for quantity, _price in fills)
        acquired = self.quantity_acquired.value()
        remaining = max(0, acquired - bought)
        average = (
            round(sum(quantity * price for quantity, price in fills) / bought)
            if bought
            else None
        )
        average_text = f" • weighted average {_gp(average)}" if average is not None else ""
        self.buy_fill_summary.setText(
            f"Bought {bought:,} of {acquired:,} • {remaining:,} remaining{average_text}"
        )
        self.buy_fill_quantity.setMaximum(max(1, remaining))
        self.buy_fill_quantity.setValue(max(1, remaining))

    def _append_sale_fill(self, quantity: int, price: int) -> None:
        row = self.sale_fills_table.rowCount()
        self.sale_fills_table.insertRow(row)
        quantity_cell = QTableWidgetItem(f"{quantity:,}")
        quantity_cell.setData(Qt.ItemDataRole.UserRole, quantity)
        price_cell = QTableWidgetItem(_gp(price))
        price_cell.setData(Qt.ItemDataRole.UserRole, price)
        self.sale_fills_table.setItem(row, 0, quantity_cell)
        self.sale_fills_table.setItem(row, 1, price_cell)

    def sale_fills(self) -> list[tuple[int, int]]:
        return [
            (
                int(self.sale_fills_table.item(row, 0).data(Qt.ItemDataRole.UserRole)),
                int(self.sale_fills_table.item(row, 1).data(Qt.ItemDataRole.UserRole)),
            )
            for row in range(self.sale_fills_table.rowCount())
        ]

    def _add_sale_fill(self) -> None:
        quantity = self.sale_fill_quantity.value()
        existing = sum(fill_quantity for fill_quantity, _price in self.sale_fills())
        if existing + quantity > self.quantity_acquired.value():
            QMessageBox.warning(
                self,
                "Too many items",
                "Sale-fill quantities cannot exceed the acquired quantity.",
            )
            return
        self._append_sale_fill(quantity, self.sale_fill_price.value())
        self._update_sale_fill_summary()

    def _remove_sale_fill(self) -> None:
        row = self.sale_fills_table.currentRow()
        if row >= 0:
            self.sale_fills_table.removeRow(row)
            self._update_sale_fill_summary()

    def _update_sale_fill_summary(self) -> None:
        fills = self.sale_fills()
        sold = sum(quantity for quantity, _price in fills)
        acquired = self.quantity_acquired.value()
        remaining = max(0, acquired - sold)
        average = (
            round(sum(quantity * price for quantity, price in fills) / sold)
            if sold
            else None
        )
        average_text = f" • weighted average {_gp(average)}" if average is not None else ""
        self.sale_fill_summary.setText(
            f"Sold {sold:,} of {acquired:,} • {remaining:,} remaining{average_text}"
        )
        self.sale_fill_quantity.setMaximum(max(1, remaining))
        self.sale_fill_quantity.setValue(max(1, remaining))

    def _accept_if_valid(self) -> None:
        quantity = self.quantity_acquired.value()
        status = self.status.currentText()
        bought = sum(fill_quantity for fill_quantity, _price in self.buy_fills())
        sold = sum(fill_quantity for fill_quantity, _price in self.sale_fills())
        if status == "Completed" and sold != quantity:
            QMessageBox.warning(
                self,
                "Unaccounted quantity",
                "A completed trade needs sale fills for the full acquired quantity.",
            )
            return
        if status == "Completed" and bought != quantity:
            QMessageBox.warning(
                self,
                "Unaccounted quantity",
                "A completed trade needs buy fills for the full acquired quantity.",
            )
            return
        self.accept()


class SortableTableItem(QTableWidgetItem):
    """Display formatted text while sorting by its underlying numeric meaning."""

    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.sort_value = _table_sort_value(value)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortableTableItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class ResponsiveTableWidget(QTableWidget):
    """Fill wide viewports while preserving readable widths in compact windows."""

    def __init__(self, column_count: int) -> None:
        super().__init__(0, column_count)
        self._preferred_widths: list[int] = []
        self._applying_widths = False
        self._user_resized_columns: set[int] = set()
        self.horizontalHeader().sectionResized.connect(self._section_resized)

    def _section_resized(self, column: int, _old_width: int, _new_width: int) -> None:
        if not self._applying_widths:
            self._user_resized_columns.add(column)

    def begin_bulk_resize(self) -> None:
        """Suspend user-resize tracking around programmatic sizing (e.g. initial layout)."""
        self._applying_widths = True

    def end_bulk_resize(self) -> None:
        self._applying_widths = False

    def set_preferred_widths(self, widths: list[int]) -> None:
        self._preferred_widths = widths
        self._apply_responsive_widths()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._apply_responsive_widths()

    def _apply_responsive_widths(self) -> None:
        if self._applying_widths or not self._preferred_widths:
            return
        self._applying_widths = True
        try:
            flexible_columns = [
                column
                for column in range(len(self._preferred_widths))
                if column not in self._user_resized_columns
            ]
            if not flexible_columns:
                return
            locked_width = sum(self.columnWidth(column) for column in self._user_resized_columns)
            preferred_total = sum(self._preferred_widths[column] for column in flexible_columns)
            available = max(0, self.viewport().width() - 2 - locked_width)
            widths = dict(enumerate(self._preferred_widths))
            if available > preferred_total > 0:
                extra = available - preferred_total
                distributed = 0
                for index, column in enumerate(flexible_columns):
                    preferred = self._preferred_widths[column]
                    if index == len(flexible_columns) - 1:
                        addition = extra - distributed
                    else:
                        addition = round(extra * preferred / preferred_total)
                        distributed += addition
                    widths[column] += addition
            for column in flexible_columns:
                self.setColumnWidth(column, widths[column])
        finally:
            self._applying_widths = False


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
        self._account_thread: QThread | None = None
        self._account_worker: AccountWorker | None = None
        self._account_lookup_name = ""
        self._account_lookup_automatic = False
        self._failed_auto_accounts: set[str] = set()
        self._profile: PlayerProfile | None = None
        self._mappings: dict[int, ItemMapping] = {}
        self._points: list[MarketPoint] = []
        self._flips: list[FlipCandidate] = []
        self._portfolio: list[FlipCandidate] = []
        self._theme = str(QSettings().value("appearance/theme", "Dark"))
        saved_watchlist = QSettings().value("market/watchlist", [])
        if not isinstance(saved_watchlist, list):
            saved_watchlist = [saved_watchlist]
        self._watchlist = {int(item_id) for item_id in saved_watchlist if str(item_id).isdigit()}
        saved_database_path = QSettings().value("journal/database_path")
        try:
            self._journal = JournalRepository(
                Path(str(saved_database_path)) if saved_database_path else None
            )
        except OSError:
            # The configured location may be unavailable (e.g. a removed drive). Fall
            # back to the default so the app can still start.
            self._journal = JournalRepository()
        self._sync_importer = RuneLiteSyncImporter()
        self._last_sync_message = ""
        self._profit_color = "#70d6a1"
        self._loss_color = "#ef6b73"
        self._muted_color = "#91a0b4"
        self._link_color = "#65a9ff"
        self._journal_status_colors = {
            "Pending buy": "#65a9ff",
            "Bought": "#d5ad52",
            "Listed for sale": "#b49af5",
            "Partially sold": "#e7a35a",
            "Completed": self._profit_color,
            "Completed (manual)": self._profit_color,
            "Cancelled": self._muted_color,
        }
        self._market_buttons: list[QPushButton] = []
        self._cash_debounce = QTimer(self)
        self._cash_debounce.setSingleShot(True)
        self._cash_debounce.setInterval(200)
        self._cash_debounce.timeout.connect(self._cash_changed)
        self._build_ui()
        self._apply_theme(self._theme)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(3_000)
        self._sync_timer.timeout.connect(self._import_runelite_events)
        self._sync_timer.start()
        QTimer.singleShot(100, self._import_runelite_events)
        QTimer.singleShot(250, self.load_market)
        self._market_timer = QTimer(self)
        self._market_timer.setInterval(5 * 60 * 1_000)
        self._market_timer.timeout.connect(self.load_market)
        self._market_timer.start()

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
        self.market_status = QLabel("Fetching market data…", objectName="status")
        self.account_label = QLabel("No character connected", objectName="muted")
        self.account_button = QPushButton("Connect character", objectName="secondary")
        self.account_button.clicked.connect(self.connect_character)
        self.runelite_button = QPushButton("Connect RuneLite", objectName="secondary")
        self.runelite_button.clicked.connect(self.open_runelite_connection)
        top.addWidget(self.market_status)
        top.addStretch()
        top.addWidget(self.runelite_button)
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
        heading = QHBoxLayout()
        heading.addWidget(QLabel(title, objectName="title"))
        heading.addStretch()
        refresh = QPushButton("Refresh market", objectName="refreshButton")
        refresh.setToolTip("Download the latest market snapshot for every page")
        refresh.clicked.connect(self.load_market)
        self._market_buttons.append(refresh)
        heading.addWidget(refresh)
        layout.addLayout(heading)
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
        self.search = SearchLineEdit("Search items…")
        self.search.textChanged.connect(self._render_flips)
        self.cash = QSpinBox()
        self.cash.setRange(10_000, 2_000_000_000)
        self.cash.setValue(10_000_000)
        self.cash.setSingleStep(1_000_000)
        self.cash.setGroupSeparatorShown(True)
        self.cash.valueChanged.connect(self._schedule_cash_changed)
        self.slots = QSpinBox()
        self.slots.setRange(1, 8)
        self.slots.setValue(_setting_int("market/ge_slots", 8, minimum=1, maximum=8))
        self.slots.valueChanged.connect(self._slots_changed)
        self.strategy = QComboBox()
        self.strategy.addItems(list(STRATEGIES))
        self.strategy.setCurrentText("Balanced (1–4h)")
        self.strategy.currentTextChanged.connect(self._strategy_changed)
        controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("Cash stack"))
        controls.addWidget(self.cash)
        controls.addWidget(QLabel("GE slots"))
        controls.addWidget(self.slots)
        controls.addWidget(self.strategy)
        layout.addLayout(controls)
        self.flip_recommendation = QLabel("Waiting for market prices…", objectName="recommendation")
        self.flip_recommendation.setWordWrap(True)
        self.flip_recommendation.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.flip_recommendation.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout.addWidget(self.flip_recommendation)
        self.track_top_button = QPushButton("Track recommended offers")
        self.track_top_button.setEnabled(False)
        self.track_top_button.clicked.connect(self._track_portfolio)
        layout.addWidget(self.track_top_button)
        self.flip_table = self._table(
            ["Item", "Buy", "Sell", "Safe max", "Profit ea.", "ROI", "1h volume", "Limit", "Max potential", "Confidence"],
            minimum_widths={0: 220},
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
            ["Item", "Buy", "Sell", "Net margin", "ROI", "1h volume", "Oldest trade"],
            minimum_widths={0: 220},
        )
        self.watchlist_table.cellDoubleClicked.connect(self._open_table_item)
        layout.addWidget(self.watchlist_table, 1)
        page.setLayout(layout)
        return page

    def _build_journal_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Trade Journal",
            "Track planned flips and review trades imported automatically from RuneLite.",
        )
        summary = QHBoxLayout()
        self.journal_profit = QLabel("Realized profit\n0 gp", objectName="summaryCard")
        self.journal_win_rate = QLabel("Win rate\n—", objectName="summaryCard")
        self.journal_invested = QLabel("Capital traded\n0 gp", objectName="summaryCard")
        summary.addWidget(self.journal_profit)
        summary.addWidget(self.journal_win_rate)
        summary.addWidget(self.journal_invested)
        summary.addStretch()
        summary.addWidget(QLabel("Period", objectName="muted"))
        self.journal_period_filter = QComboBox()
        self.journal_period_filter.addItems(PERIOD_FILTERS)
        saved_period = str(QSettings().value("journal/period_filter", PERIOD_FILTERS[0]))
        self.journal_period_filter.setCurrentText(
            saved_period if saved_period in PERIOD_FILTERS else PERIOD_FILTERS[0]
        )
        self.journal_period_filter.setMinimumWidth(140)
        self.journal_period_filter.setToolTip(
            "Limit the summary cards and completed/cancelled rows below to this window. "
            "Trades still in progress always stay visible."
        )
        self.journal_period_filter.currentTextChanged.connect(
            self._journal_period_filter_changed
        )
        summary.addWidget(self.journal_period_filter)
        layout.addLayout(summary)

        self.journal_tabs = QTabWidget()
        plans_tab = QWidget()
        plans_layout = QVBoxLayout(plans_tab)
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
        actions.addWidget(QLabel("Status", objectName="muted"))
        self.journal_status_filter = QComboBox()
        self.journal_status_filter.addItems(JOURNAL_STATUS_FILTERS)
        saved_filter = str(
            QSettings().value("journal/status_filter", JOURNAL_STATUS_FILTERS[0])
        )
        self.journal_status_filter.setCurrentText(
            saved_filter if saved_filter in JOURNAL_STATUS_FILTERS else JOURNAL_STATUS_FILTERS[0]
        )
        self.journal_status_filter.setMinimumWidth(160)
        self.journal_status_filter.setToolTip(
            "Show every journal entry or focus on one stage of the trade lifecycle."
        )
        self.journal_status_filter.currentTextChanged.connect(
            self._journal_status_filter_changed
        )
        actions.addWidget(self.journal_status_filter)
        plans_layout.addLayout(actions)
        self.journal_filter_empty = QLabel(
            "No journal entries match this status and period filter.", objectName="status"
        )
        plans_layout.addWidget(self.journal_filter_empty)
        self.journal_table = self._table(
            ["Date", "Status", "Item", "Qty", "Buy suggestion", "Actual buy", "Sell suggestion", "Actual sell", "P/L"],
            minimum_widths={0: 105, 1: 155, 2: 230, 8: 130},
        )
        self.journal_table.cellDoubleClicked.connect(
            lambda _row, _column: self._update_selected_trade()
        )
        plans_layout.addWidget(self.journal_table, 1)
        self.journal_tabs.addTab(plans_tab, "Plans && completed")

        activity_tab = QWidget()
        activity_layout = QVBoxLayout(activity_tab)
        sync_controls = QHBoxLayout()
        self.runelite_status = QLabel("RuneLite not connected", objectName="status")
        self.runelite_filter = QComboBox()
        self.runelite_filter.addItems(["All activity", "Grand Exchange", "Player trades"])
        self.runelite_filter.currentTextChanged.connect(self._render_synced_trades)
        import_button = QPushButton("Import now", objectName="secondary")
        import_button.clicked.connect(self._import_runelite_events)
        details_button = QPushButton("View details", objectName="secondary")
        details_button.clicked.connect(self._open_selected_synced_trade)
        remove_button = QPushButton("Delete entry", objectName="secondary")
        remove_button.clicked.connect(self._delete_selected_synced_trade)
        sync_controls.addWidget(self.runelite_status, 1)
        sync_controls.addWidget(self.runelite_filter)
        sync_controls.addWidget(import_button)
        sync_controls.addWidget(details_button)
        sync_controls.addWidget(remove_button)
        activity_layout.addLayout(sync_controls)
        self.synced_trade_table = self._table(
            ["Time", "Source", "Character", "Trade", "Given", "Received", "Est. difference"],
            minimum_widths={0: 145, 2: 140, 3: 220, 4: 180, 5: 180, 6: 130},
        )
        self.synced_trade_table.cellDoubleClicked.connect(
            lambda _row, _column: self._open_selected_synced_trade()
        )
        activity_layout.addWidget(self.synced_trade_table, 1)
        self.journal_tabs.addTab(activity_tab, "RuneLite activity")
        layout.addWidget(self.journal_tabs, 1)
        page.setLayout(layout)
        self._render_journal()
        self._render_synced_trades()
        return page

    def _build_alch_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Alch Finder",
            "Find liquid High Alchemy candidates using conservative recent prices, buy limits, and your available cash.",
        )
        controls = QHBoxLayout()
        self.alch_budget = QSpinBox()
        self.alch_budget.setRange(10_000, 2_000_000_000)
        self.alch_budget.setValue(
            _setting_int("alch/budget", 2_000_000, minimum=10_000, maximum=2_000_000_000)
        )
        self.alch_budget.setSingleStep(100_000)
        self.alch_budget.setGroupSeparatorShown(True)
        self.alch_budget.valueChanged.connect(self._alch_settings_changed)
        self.alch_safety = QComboBox()
        self.alch_safety.addItems(list(ALCH_POLICIES))
        saved_policy = str(QSettings().value("alch/safety", "Safer"))
        self.alch_safety.setCurrentText(
            saved_policy if saved_policy in ALCH_POLICIES else "Safer"
        )
        self.alch_safety.currentTextChanged.connect(self._alch_settings_changed)
        controls.addWidget(QLabel("Budget"))
        controls.addWidget(self.alch_budget)
        controls.addWidget(QLabel("Safety"))
        controls.addWidget(self.alch_safety)
        controls.addStretch()
        layout.addLayout(controls)
        self.alch_note = QLabel(objectName="recommendation")
        self.alch_note.setWordWrap(True)
        layout.addWidget(self.alch_note)
        self.alch_table = self._table(
            [
                "Item",
                "Latest trade",
                "Safe buy",
                "High alch",
                "Rune",
                "Profit/alch",
                "Safe qty",
                "Capital",
                "Est. GP/hr",
                "ROI",
                "1h volume",
                "Buy trade age",
                "Limit",
            ],
            minimum_widths={0: 230, 1: 105, 2: 95, 3: 95, 5: 105, 7: 110, 8: 115, 10: 100},
        )
        self.alch_table.cellDoubleClicked.connect(self._open_table_item)
        layout.addWidget(self.alch_table, 1)
        page.setLayout(layout)
        return page

    def _build_skilling_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Skilling Profit",
            "Compare processing and gathering activities across ten skills using current prices.",
        )
        controls = QHBoxLayout()
        self.skill_search = SearchLineEdit("Search methods…")
        self.skill_search.textChanged.connect(self._render_skilling)
        self.skill_filter = QComboBox()
        self.skill_filter.addItem("All skills")
        self.skill_filter.addItems(sorted({method.skill for method in SKILL_METHODS}))
        self.skill_filter.currentTextChanged.connect(self._render_skilling)
        self.skill_profitable = QCheckBox("Profitable only")
        self.skill_profitable.setChecked(True)
        self.skill_profitable.toggled.connect(self._render_skilling)
        self.skill_available = QCheckBox("Meets character level")
        self.skill_available.setEnabled(False)
        self.skill_available.toggled.connect(self._render_skilling)
        controls.addWidget(self.skill_search, 1)
        controls.addWidget(self.skill_filter)
        controls.addWidget(self.skill_profitable)
        controls.addWidget(self.skill_available)
        layout.addLayout(controls)
        self.skill_note = QLabel(objectName="recommendation")
        self.skill_note.setWordWrap(True)
        layout.addWidget(self.skill_note)
        self.skill_table = self._table(
            [
                "Method",
                "Skill",
                "Level",
                "Input cost",
                "Output after tax",
                "Profit/action",
                "Actions/hr",
                "Est. GP/hr",
                "Oldest trade used",
                "Level met",
                "Assumption",
            ],
            minimum_widths={0: 250, 10: 320},
        )
        self.skill_table.cellDoubleClicked.connect(self._open_skill_guide)
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
        self.kingdom_table = self._table(
            ["Resource", "Value/worker/day", "Cost/worker/day", "Profit/worker/day"],
            minimum_widths={0: 220},
        )
        layout.addWidget(self.kingdom_table, 1)
        page.setLayout(layout)
        return page

    def _table(
        self,
        headers: list[str],
        *,
        minimum_widths: dict[int, int] | None = None,
    ) -> QTableWidget:
        table = ResponsiveTableWidget(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(64)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setProperty("minimumColumnWidths", minimum_widths or {})
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalHeader().setDefaultSectionSize(38)
        return table

    def _change_page(self, row: int) -> None:
        if row >= 0:
            self.pages.setCurrentIndex(row)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._theme, self._journal.database_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._theme = dialog.theme.currentText()
        QSettings().setValue("appearance/theme", self._theme)
        self._apply_theme(self._theme)
        if dialog.requested_database_path is not None:
            self._change_database_location(dialog.requested_database_path)

    def _change_database_location(self, new_path: Path) -> None:
        old_path = self._journal.database_path
        if new_path == old_path:
            return
        if new_path.exists():
            answer = QMessageBox.question(
                self,
                "Database already exists at this location",
                f"{new_path} already contains a database.\n\n"
                "Use the existing file there (Yes), or replace it with your current "
                "journal data (No)?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            replace_existing = answer == QMessageBox.StandardButton.No
        else:
            replace_existing = True
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if replace_existing and old_path.exists():
                shutil.copy2(old_path, new_path)
            new_repository = JournalRepository(new_path)
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not change database location", f"{exc}"
            )
            return
        self._journal = new_repository
        QSettings().setValue("journal/database_path", str(new_path))
        self._render_journal()
        self._render_synced_trades()
        QMessageBox.information(
            self,
            "Database location updated",
            f"Now using the database at:\n{new_path}",
        )

    def connect_character(self) -> None:
        dialog = AccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_account_lookup(dialog.name.text(), automatic=False)

    def _start_account_lookup(self, character_name: str, *, automatic: bool) -> None:
        clean_name = character_name.strip()
        if not clean_name or self._account_thread is not None:
            return
        self._account_lookup_name = clean_name
        self._account_lookup_automatic = automatic
        self.account_label.setText(f"Loading {clean_name}'s public hiscores…")
        self.account_button.setText("Loading character…")
        self.account_button.setEnabled(False)
        thread = QThread(self)
        worker = AccountWorker(clean_name)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._account_loaded)
        worker.failed.connect(self._account_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._account_worker_stopped)
        self._account_thread = thread
        self._account_worker = worker
        thread.start()

    def _account_loaded(self, profile: PlayerProfile) -> None:
        self._profile = profile
        self._failed_auto_accounts.discard(profile.name.casefold())
        self.account_label.setText(f"{profile.name} • Total {profile.total_level:,}")
        self.account_button.setText("Change character")
        self.account_button.setEnabled(True)
        self.skill_available.setEnabled(True)
        self._render_alch()
        self._render_skilling()

    def _account_failed(self, message: str) -> None:
        if self._account_lookup_automatic:
            self._failed_auto_accounts.add(self._account_lookup_name.casefold())
            self.account_label.setText(
                f"RuneLite: {self._account_lookup_name} • public hiscores unavailable"
            )
        else:
            QMessageBox.warning(self, "Could not connect character", message)
            self.account_label.setText("No character connected")
        self.account_button.setText("Connect character" if self._profile is None else "Change character")
        self.account_button.setEnabled(True)

    def _account_worker_stopped(self) -> None:
        if self._account_thread is not None:
            self._account_thread.deleteLater()
        self._account_worker = None
        self._account_thread = None

    def open_runelite_connection(self) -> None:
        dialog = RuneLiteConnectionDialog(self._sync_importer, self)
        dialog.exec()
        self._import_runelite_events()

    def load_market(self) -> None:
        if self._thread is not None:
            return
        for button in self._market_buttons:
            button.setEnabled(False)
            button.setText("Refreshing…")
        self.market_status.setText("Fetching market data…")
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
        self._refresh_overnight_suggestions()
        # The cash field may have changed while the download was in progress.
        self._flips = rank_flips(
            mappings,
            points,
            cash_stack=self.cash.value(),
            strategy=self.strategy.currentText(),
            slot_count=self.slots.value(),
        )
        self._plan_portfolio()
        for button in self._market_buttons:
            button.setEnabled(True)
            button.setText("Refresh market")
        source = "Cached market data loaded" if used_cache else "Market data fetched"
        self.market_status.setText(f"{source} • {datetime.now().astimezone():%H:%M:%S}")
        self._render_flips()
        self._render_alch()
        self._render_watchlist()
        self._render_journal()
        self._render_skilling()
        self._render_kingdom()

    def _schedule_cash_changed(self, _cash_stack: int) -> None:
        """Coalesce rapid spinbox ticks (e.g. holding an arrow key) into one re-rank."""
        self._cash_debounce.start()

    def _cash_changed(self) -> None:
        """Re-rank the local snapshot; no market download is needed."""
        if not self._points:
            return
        self._flips = rank_flips(
            self._mappings,
            self._points,
            cash_stack=self.cash.value(),
            strategy=self.strategy.currentText(),
            slot_count=self.slots.value(),
        )
        self._plan_portfolio()
        self._render_flips()

    def _slots_changed(self, slot_count: int) -> None:
        QSettings().setValue("market/ge_slots", slot_count)
        if self._points:
            self._flips = rank_flips(
                self._mappings,
                self._points,
                cash_stack=self.cash.value(),
                strategy=self.strategy.currentText(),
                slot_count=slot_count,
            )
        self._plan_portfolio()
        self._render_flips()

    def _alch_settings_changed(self, _value: object) -> None:
        QSettings().setValue("alch/budget", self.alch_budget.value())
        QSettings().setValue("alch/safety", self.alch_safety.currentText())
        self._render_alch()

    def _strategy_changed(self, strategy: str) -> None:
        if not self._points:
            return
        self._flips = rank_flips(
            self._mappings,
            self._points,
            cash_stack=self.cash.value(),
            strategy=strategy,
            slot_count=self.slots.value(),
        )
        self._plan_portfolio()
        self._render_flips()

    def _plan_portfolio(self) -> None:
        self._portfolio = plan_flip_portfolio(
            self._flips,
            cash_stack=self.cash.value(),
            slot_count=self.slots.value(),
        )

    def _market_failed(self, message: str) -> None:
        for button in self._market_buttons:
            button.setEnabled(True)
            button.setText("Refresh market")
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
        if self._portfolio:
            self.track_top_button.setEnabled(True)
            self.track_top_button.setText(
                f"Track all {len(self._portfolio)} recommended offers"
            )
            mode = self.strategy.currentText()
            allocated = sum(item.capital_required for item in self._portfolio)
            expected_profit = sum(item.potential_profit for item in self._portfolio)
            remaining = self.cash.value() - allocated
            plans = [
                (
                    f"Our recommendations — {mode} • "
                    f"{len(self._portfolio)}/{self.slots.value()} GE slots • "
                    f"{_gp(allocated)} allocated • {_gp(remaining)} held back"
                )
            ]
            for number, item in enumerate(self._portfolio, start=1):
                plans.append(
                    f"{number}. BUY {item.suggested_quantity:,} × {item.name} at "
                    f"{_gp(item.buy_price)} each ({_gp(item.capital_required)} total); "
                    f"THEN SELL at {_gp(item.sell_price)} each. "
                    f"Est. {_gp(item.potential_profit)} after tax; {item.confidence}% confidence."
                )
            plans.append(
                f"Combined estimate if every buy and sell fills: {_gp(expected_profit)}. "
                "Held-back cash has no qualifying size under the current liquidity, limit, and "
                "risk caps. Targets are patient limit offers; fills are not guaranteed."
            )
            self.flip_recommendation.setText("\n".join(plans))
        else:
            self.track_top_button.setEnabled(False)
            self.track_top_button.setText("Track recommended offers")
            self.flip_recommendation.setText(
                "No opportunity currently passes this mode’s affordability, liquidity, stability, and confidence rules."
            )

    def _render_alch(self) -> None:
        magic_level = self._profile.skills.get("Magic") if self._profile else None
        rows = alch_candidates(
            self._mappings,
            self._points,
            cash_stack=self.alch_budget.value(),
            policy=self.alch_safety.currentText(),
            magic_level=magic_level,
        )[:300]
        values = [
            [
                row.name,
                _gp(row.latest_buy_price),
                _gp(row.buy_price),
                _gp(row.alch_value),
                _gp(row.rune_cost),
                _gp(row.profit),
                f"{row.safe_quantity:,}",
                _gp(row.capital_required),
                _gp(row.hourly_profit),
                f"{row.roi:.2f}%",
                f"{row.volume:,}",
                _short_duration(row.age_seconds),
                f"{row.buy_limit:,}",
            ]
            for row in rows
        ]
        already_sorted = bool(self.alch_table.property("columnsInitialized"))
        self._fill_table(
            self.alch_table,
            values,
            green_columns={5, 8, 9},
            row_ids=[row.item_id for row in rows],
        )
        if not already_sorted:
            self.alch_table.sortItems(8, Qt.SortOrder.DescendingOrder)
        selected = ALCH_POLICIES[self.alch_safety.currentText()]
        age_text = (
            _short_duration(selected.max_age_seconds)
            if selected.max_age_seconds is not None
            else "unlimited age"
        )
        self.alch_note.setText(
            f"{len(rows)} candidates pass {self.alch_safety.currentText().lower()} checks. "
            "Safe buy uses the highest of the latest trade, five-minute average, and "
            "one-hour average—not an advertised GE offer. "
            f"This mode requires {selected.min_volume:,}+ trades/hour, {age_text} maximum "
            f"price age, and caps quantity at {selected.volume_share:.0%} of hourly volume, "
            "the GE buy limit, 1,200 casts, and your budget. Test a small buy first."
        )

    def _refresh_overnight_suggestions(self) -> None:
        """Review open overnight targets once per local calendar day."""
        now = datetime.now().astimezone()
        point_by_id = {point.item_id: point for point in self._points}
        for trade in self._journal.list_tracked():
            if (
                not trade.strategy.startswith("Overnight")
                or trade.status in {"Completed", "Cancelled"}
                or trade.item_id is None
            ):
                continue
            try:
                last_review = datetime.fromisoformat(
                    trade.suggestion_reviewed_at or trade.created_at
                ).astimezone()
            except ValueError:
                last_review = now.replace(year=1970)
            if last_review.date() >= now.date():
                continue
            point = point_by_id.get(trade.item_id)
            if point is None:
                continue
            age = int(now.timestamp()) - min(point.high_time, point.low_time)
            if age < 0 or age > 1_800:
                continue
            buy_price, sell_price = offer_targets(point)
            self._journal.review_suggestion(
                trade.position_id,
                buy_price,
                sell_price,
                now.isoformat(timespec="seconds"),
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

    def _journal_status_filter_changed(self, selected_filter: str) -> None:
        QSettings().setValue("journal/status_filter", selected_filter)
        self._render_journal()

    def _journal_period_filter_changed(self, selected_period: str) -> None:
        QSettings().setValue("journal/period_filter", selected_period)
        self._render_journal()

    def _render_journal(self) -> None:
        tracked = self._journal.list_tracked()
        trades = self._journal.list_all()
        selected_filter = self.journal_status_filter.currentText()
        selected_period = self.journal_period_filter.currentText()
        now = datetime.now(UTC)
        rendered_rows: list[tuple[list[str], int, str, JournalPLPresentation]] = []
        for trade in tracked:
            if not journal_status_matches(trade.status, selected_filter):
                continue
            # Active positions have no completed_at yet, so the period filter — which
            # scopes *history* — never hides them; only finished (completed/cancelled)
            # rows are subject to it, matching the summary cards above.
            if trade.completed_at is not None and not trade_within_period(
                trade.completed_at, selected_period, now
            ):
                continue
            profit = journal_pl_presentation(
                trade.status,
                trade.estimated_profit,
                trade.realized_profit,
                trade.remaining_quantity,
            )
            rendered_rows.append(
                (
                    [
                        trade.created_at[:10],
                        f"● {trade.status}",
                        trade.item_name,
                        f"{trade.quantity:,}",
                        f"{_gp(trade.buy_suggestion)} ↻"
                        if trade.suggestion_was_refreshed
                        else _gp(trade.buy_suggestion),
                        f"{_gp(trade.average_buy_price)} avg"
                        if trade.average_buy_price is not None
                        else "—",
                        f"{_gp(trade.sell_suggestion)} ↻"
                        if trade.suggestion_was_refreshed
                        else _gp(trade.sell_suggestion),
                        f"{_gp(trade.average_sell_price)} avg"
                        if trade.average_sell_price is not None
                        else "—",
                        profit.text,
                    ],
                    trade.position_id,
                    trade.status,
                    profit,
                )
            )
        for trade in trades:
            raw_status = "Completed (manual)"
            if not journal_status_matches(raw_status, selected_filter):
                continue
            if not trade_within_period(trade.recorded_at, selected_period, now):
                continue
            profit = journal_pl_presentation(
                raw_status,
                trade.profit,
                trade.profit,
            )
            rendered_rows.append(
                (
                    [
                        trade.recorded_at[:10],
                        f"● {raw_status}",
                        trade.item_name,
                        f"{trade.quantity:,}",
                        _gp(trade.buy_price),
                        _gp(trade.buy_price),
                        _gp(trade.sell_price),
                        _gp(trade.sell_price),
                        profit.text,
                    ],
                    trade.trade_id,
                    raw_status,
                    profit,
                )
            )

        values = [row for row, _record_id, _status, _profit in rendered_rows]
        row_ids = [record_id for _row, record_id, _status, _profit in rendered_rows]
        self.journal_filter_empty.setVisible(not values)
        profit_by_record = {
            (raw_status, record_id): profit
            for _row, record_id, raw_status, profit in rendered_rows
        }
        self._fill_table(
            self.journal_table,
            values,
            green_columns=set(),
            row_ids=row_ids,
        )
        for row in range(self.journal_table.rowCount()):
            record_id = self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            status_cell = self.journal_table.item(row, 1)
            raw_status = status_cell.text().removeprefix("● ")
            status_cell.setData(Qt.ItemDataRole.UserRole, raw_status)
            status_cell.setToolTip(raw_status)
            if isinstance(status_cell, SortableTableItem):
                status_cell.sort_value = (
                    _JOURNAL_STATUS_ORDER.get(raw_status, len(_JOURNAL_STATUS_ORDER)),
                    raw_status,
                )
            status_cell.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            status_font = status_cell.font()
            status_font.setBold(True)
            status_cell.setFont(status_font)
            status_cell.setForeground(
                QColor(self._journal_status_colors.get(raw_status, self._muted_color))
            )
            self.journal_table.item(row, 2).setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

            profit = profit_by_record.get((raw_status, record_id))
            if profit is None:
                continue
            profit_cell = self.journal_table.item(row, 8)
            profit_cell.setData(Qt.ItemDataRole.UserRole, profit.tone)
            if profit.tone == "positive":
                profit_cell.setForeground(QColor(self._profit_color))
            elif profit.tone == "negative":
                profit_cell.setForeground(QColor(self._loss_color))
            elif profit.tone == "muted":
                profit_cell.setForeground(QColor(self._muted_color))
            profit_cell.setToolTip(profit.tooltip)

        period_trades = [
            trade for trade in trades if trade_within_period(trade.recorded_at, selected_period, now)
        ]
        realized_positions = [
            trade
            for trade in tracked
            if trade.realized_profit is not None
            and trade_within_period(trade.completed_at, selected_period, now)
        ]
        profits = [trade.profit for trade in period_trades] + [
            trade.realized_profit or 0 for trade in realized_positions
        ]
        total_profit = sum(profits)
        total_invested = sum(trade.invested for trade in period_trades) + sum(
            trade.invested for trade in realized_positions
        )
        wins = sum(profit > 0 for profit in profits)
        win_rate = wins / len(profits) * 100 if profits else 0
        self.journal_profit.setText(f"Realized profit\n{_signed_gp(total_profit)}")
        self.journal_profit.setProperty(
            "moneyState",
            "positive" if total_profit > 0 else "negative" if total_profit < 0 else "neutral",
        )
        self.journal_profit.style().unpolish(self.journal_profit)
        self.journal_profit.style().polish(self.journal_profit)
        self.journal_win_rate.setText(
            f"Win rate\n{win_rate:.1f}%" if profits else "Win rate\n—"
        )
        self.journal_invested.setText(f"Capital traded\n{_gp(total_invested)}")

    def _import_runelite_events(self) -> None:
        try:
            result = self._sync_importer.import_pending(self._journal)
        except Exception as exc:  # noqa: BLE001 - a transient local failure must not break the UI loop.
            self._last_sync_message = f" • sync paused: {exc}"
            self._update_runelite_status()
            return
        if result.imported:
            label = "trade" if result.imported == 1 else "trades"
            self._last_sync_message = f" • imported {result.imported} new {label}"
        elif result.rejected:
            self._last_sync_message = (
                f" • {result.rejected} invalid event"
                f"{'s' if result.rejected != 1 else ''} quarantined"
            )
        elif result.duplicates:
            self._last_sync_message = " • already up to date"
        self._update_runelite_status()
        if result.imported or result.duplicates:
            self._render_synced_trades()

    def _update_runelite_status(self) -> None:
        connection = self._sync_importer.connection_status()
        if connection.active:
            button_text = "RuneLite connected"
            character = f" as {connection.account_name}" if connection.account_name else ""
            player_trades = "on" if connection.player_trade_tracking else "off"
            status_text = (
                f"RuneLite connected{character} • syncing automatically • "
                f"player trades {player_trades}"
            )
        elif connection.detected:
            button_text = "RuneLite offline"
            status_text = "RuneLite offline • saved activity will import when available"
        else:
            button_text = "Connect RuneLite"
            status_text = "Connect RuneLite to import GE fills and optional player trades"
        self.runelite_button.setText(button_text)
        self.runelite_status.setText(status_text + self._last_sync_message)
        account_name = connection.account_name
        profile_name = self._profile.name.casefold() if self._profile else None
        if (
            connection.active
            and account_name is not None
            and account_name.casefold() != profile_name
            and account_name.casefold() not in self._failed_auto_accounts
            and self._account_thread is None
        ):
            self._start_account_lookup(account_name, automatic=True)

    def _render_synced_trades(self) -> None:
        event_type = {
            "Grand Exchange": "ge_fill",
            "Player trades": "player_trade",
        }.get(self.runelite_filter.currentText())
        trades = self._journal.list_synced_trades(event_type)
        values = [
            [
                _display_timestamp(trade.occurred_at),
                trade.source,
                trade.account_name,
                _synced_trade_label(trade),
                _compact_items(trade.given),
                _compact_items(trade.received),
                _signed_gp(trade.estimated_difference)
                if trade.event_type == "player_trade"
                else "—",
            ]
            for trade in trades
        ]
        self._fill_table(
            self.synced_trade_table,
            values,
            green_columns=set(),
            row_ids=[trade.event_id for trade in trades],
        )

    def _selected_synced_trade(self) -> SyncedTrade | None:
        row = self.synced_trade_table.currentRow()
        if row < 0:
            return None
        event_id = self.synced_trade_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(event_id, str):
            return None
        return self._journal.get_synced_trade(event_id)

    def _open_selected_synced_trade(self) -> None:
        trade = self._selected_synced_trade()
        if trade is None:
            QMessageBox.information(self, "No activity selected", "Select a RuneLite activity row first.")
            return
        SyncedTradeDetailsDialog(trade, self).exec()

    def _delete_selected_synced_trade(self) -> None:
        trade = self._selected_synced_trade()
        if trade is None:
            QMessageBox.information(self, "No activity selected", "Select a RuneLite activity row first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete imported activity",
            "Remove this imported trade from the journal? This does not affect RuneLite or the game.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._journal.delete_synced_trade(trade.event_id)
            self._render_synced_trades()

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

    def _track_portfolio(self) -> None:
        if not self._portfolio:
            return
        for candidate in self._portfolio:
            self._journal.track(
                candidate.item_id,
                candidate.name,
                candidate.suggested_quantity,
                candidate.buy_price,
                candidate.sell_price,
                self.strategy.currentText(),
            )
        self._render_journal()
        self.nav.setCurrentRow(2)

    def _track_candidate(self, candidate: FlipCandidate) -> None:
        self._journal.track(
            candidate.item_id,
            candidate.name,
            candidate.suggested_quantity,
            candidate.buy_price,
            candidate.sell_price,
            self.strategy.currentText(),
        )
        self._render_journal()
        self.nav.setCurrentRow(2)

    def _delete_selected_trade(self) -> None:
        row = self.journal_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No trade selected", "Select a journal row first.")
            return
        record_id = self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        status = self.journal_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
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
        status = self.journal_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
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
        try:
            self._journal.update_tracked(
                position_id,
                new_status,
                None,
                None,
                dialog.sale_fills(),
                dialog.buy_fills(),
                dialog.quantity_acquired.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not save trade", str(exc))
            return
        self._render_journal()

    def _render_skilling(self) -> None:
        skills = self._profile.skills if self._profile else {}
        priced_rows = skill_results(self._mappings, self._points, skills)
        query = self.skill_search.text().strip().casefold()
        selected_skill = self.skill_filter.currentText()
        rows = [
            row
            for row in priced_rows
            if query in row.name.casefold()
            and (selected_skill == "All skills" or row.skill == selected_skill)
            and (not self.skill_profitable.isChecked() or row.profit_hour > 0)
            and (not self.skill_available.isChecked() or row.eligible is True)
        ]
        values = [
            [
                row.name,
                row.skill,
                str(row.level),
                _gp(row.input_cost),
                _gp(row.output_value),
                _signed_gp(row.profit_action),
                f"{row.actions_hour:,}",
                _signed_gp(row.profit_hour),
                _short_duration(row.price_age_seconds),
                _availability(row.eligible),
                f"{row.notes or 'Rate estimate'} · OSRS Wiki guide ↗",
            ]
            for row in rows
        ]
        already_sorted = bool(self.skill_table.property("columnsInitialized"))
        self._fill_table(self.skill_table, values, green_columns={5, 7})
        for row_index, row in enumerate(rows):
            guide_cell = self.skill_table.item(row_index, 10)
            guide_cell.setData(Qt.ItemDataRole.UserRole, row.guide_url)
            guide_cell.setToolTip(
                f"{row.notes or 'Actions per hour are practical estimates.'}\n"
                "Click to open the relevant OSRS Wiki training guide."
            )
            guide_cell.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            guide_cell.setForeground(QColor(self._link_color))
            link_font = guide_cell.font()
            link_font.setUnderline(True)
            guide_cell.setFont(link_font)
        if not already_sorted:
            self.skill_table.sortItems(7, Qt.SortOrder.DescendingOrder)
        profile_text = (
            f" Requirements checked against {self._profile.name}'s public hiscores."
            if self._profile
            else " Connect a character to filter methods by public skill levels."
        )
        self.skill_note.setText(
            f"Showing {len(rows)} of {len(priced_rows)} currently priced methods from a "
            f"{len(SKILL_METHODS)}-method catalogue.{profile_text} Inputs use conservative "
            "buyer-paid prices; outputs use conservative seller-received prices after GE tax. "
            "Rates are practical estimates, not guarantees—check each assumption before buying supplies."
        )

    def _open_skill_guide(self, row: int, column: int) -> None:
        if column != 10:
            return
        guide_url = self.skill_table.item(row, column).data(Qt.ItemDataRole.UserRole)
        if isinstance(guide_url, str) and guide_url.startswith(
            "https://oldschool.runescape.wiki/"
        ):
            QDesktopServices.openUrl(QUrl(guide_url))

    def _render_kingdom(self) -> None:
        rows = kingdom_results(self._mappings, self._points)
        values = [[row.resource, _gp(row.value_worker), _gp(row.cost_worker), _gp(row.profit_worker)] for row in rows]
        self._fill_table(self.kingdom_table, values, green_columns={3})
        if len(rows) >= 2:
            self.kingdom_recommendation.setText(
                f"Recommended: 10 workers on {rows[0].resource} and 5 on {rows[1].resource}. "
                f"Estimated primary-output profit: {_gp(rows[0].profit_worker * 10 + rows[1].profit_worker * 5)} per day."
            )
        else:
            self.kingdom_recommendation.setText(
                "Waiting for enough priced resources to recommend a worker split…"
            )

    def _fill_table(
        self,
        table: QTableWidget,
        values: list[list[str]],
        green_columns: set[int],
        row_ids: list[int | str] | None = None,
    ) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(values))
        for row_index, row in enumerate(values):
            for column, value in enumerate(row):
                cell = SortableTableItem(value)
                cell.setToolTip(value)
                if column == 0 and row_ids is not None:
                    cell.setData(Qt.ItemDataRole.UserRole, row_ids[row_index])
                if column > 0:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column in green_columns:
                    numeric = _leading_number(value)
                    if numeric > 0:
                        cell.setForeground(QColor(self._profit_color))
                    elif numeric < 0:
                        cell.setForeground(QColor(self._loss_color))
                table.setItem(row_index, column, cell)
        if values and not table.property("columnsInitialized"):
            is_responsive = isinstance(table, ResponsiveTableWidget)
            if is_responsive:
                table.begin_bulk_resize()
            table.resizeColumnsToContents()
            minimum_widths = table.property("minimumColumnWidths") or {}
            preferred_widths: list[int] = []
            for column, width in minimum_widths.items():
                table.setColumnWidth(column, max(table.columnWidth(column), width))
            for column in range(table.columnCount()):
                preferred_widths.append(table.columnWidth(column) + 14)
            if is_responsive:
                table.end_bulk_resize()
                table.set_preferred_widths(preferred_widths)
            table.setProperty("columnsInitialized", True)
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
            "Dark": (
                "#11151b", "#171c24", "#e8edf3", "#91a0b4", "#1a2029", "#28313d", "#1e252f",
                "#70d6a1", "#d5ad52", "#403a2c", "#e8edf3", "#65a9ff", "#28313d", "#91a0b4",
            ),
            "Midnight": (
                "#08111f", "#0d192b", "#e5efff", "#8fa5c4", "#122139", "#263b59", "#172942",
                "#67d7c4", "#d5ad52", "#403a2c", "#e5efff", "#65a9ff", "#263b59", "#8fa5c4",
            ),
            "Light": (
                "#f3f5f7", "#e7ebef", "#1d2833", "#5e6b78", "#ffffff", "#cbd3dc", "#dfe5eb",
                "#16845b", "#8a6500", "#dbe6f5", "#1d2833", "#1f64a8", "#d6dbe0", "#8a939c",
            ),
        }
        (
            background, sidebar, text, muted, field, border, header, profit,
            accent_text, selection_bg, selection_text, link, disabled_bg, disabled_text,
        ) = palettes.get(theme, palettes["Dark"])
        self._profit_color = profit
        self._loss_color = "#d94752" if theme == "Light" else "#ef6b73"
        self._muted_color = muted
        self._link_color = link
        if theme == "Light":
            pending, bought, listed, partial = (
                "#1f64a8",
                "#8a6500",
                "#6b4fb3",
                "#a65300",
            )
        else:
            pending, bought, listed, partial = (
                "#65a9ff",
                "#d5ad52",
                "#b49af5",
                "#e7a35a",
            )
        self._journal_status_colors = {
            "Pending buy": pending,
            "Bought": bought,
            "Listed for sale": listed,
            "Partially sold": partial,
            "Completed": self._profit_color,
            "Completed (manual)": self._profit_color,
            "Cancelled": self._muted_color,
        }
        icon_variant = "-dark" if theme == "Light" else ""
        spin_up_icon = _resource_path(f"assets/spin-up{icon_variant}.svg").as_posix()
        spin_down_icon = _resource_path(f"assets/spin-down{icon_variant}.svg").as_posix()
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
            #summaryCard[moneyState="positive"] {{ color: {profit}; }}
            #summaryCard[moneyState="negative"] {{ color: {self._loss_color}; }}
            #nav {{ background: transparent; border: 0; outline: 0; padding-top: 16px; }}
            #nav::item {{ padding: 11px 12px; border-radius: 7px; margin: 1px 2px; }}
            #nav::item:selected {{ background: {header}; color: {accent_text}; }}
            QLineEdit, QSpinBox, QComboBox {{ background: {field}; border: 1px solid {border}; border-radius: 7px; padding: 7px 10px; min-height: 22px; selection-background-color: #765f2d; }}
            QSpinBox {{ padding-right: 62px; }}
            QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 29px; border-left: 1px solid {border}; border-bottom: 1px solid {border}; border-top-right-radius: 7px; }}
            QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 29px; border-left: 1px solid {border}; border-top: 1px solid {border}; border-bottom-right-radius: 7px; }}
            QSpinBox::up-arrow {{ image: url("{spin_up_icon}"); width: 12px; height: 8px; }}
            QSpinBox::down-arrow {{ image: url("{spin_down_icon}"); width: 12px; height: 8px; }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {header}; }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid #d5ad52; }}
            QComboBox::drop-down {{ border: 0; width: 30px; }}
            QCheckBox {{ spacing: 8px; min-height: 36px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; }}
            QPushButton {{ background: #d5ad52; color: #11151b; border: 0; border-radius: 7px; padding: 10px 18px; font-weight: 650; }}
            QPushButton:hover {{ background: #e2bd64; }}
            QPushButton#secondary, QPushButton#settingsButton, QPushButton#refreshButton {{ background: {header}; color: {text}; border: 1px solid {border}; }}
            QPushButton#secondary:hover, QPushButton#settingsButton:hover, QPushButton#refreshButton:hover {{ border-color: #d5ad52; }}
            QPushButton:disabled {{ background: {disabled_bg}; color: {disabled_text}; }}
            QTableWidget {{ background: {background}; alternate-background-color: {field}; border: 1px solid {border}; border-radius: 8px; gridline-color: {border}; }}
            QHeaderView::section {{ background: {header}; color: {text}; border: 0; border-bottom: 1px solid {border}; padding: 10px; }}
            QTableWidget::item {{ padding: 7px; }}
            QTableWidget::item:hover {{ background: {header}; }}
            QTableWidget::item:selected {{ background: {selection_bg}; color: {selection_text}; }}
            QScrollBar:vertical {{ background: {background}; width: 12px; margin: 0; border-radius: 6px; }}
            QScrollBar:horizontal {{ background: {background}; height: 12px; margin: 0; border-radius: 6px; }}
            QScrollBar::handle {{ background: {border}; border-radius: 6px; min-height: 30px; min-width: 30px; }}
            QScrollBar::handle:hover {{ background: {muted}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        """)
        self._render_flips()
        self._render_alch()
        self._render_watchlist()
        self._render_journal()
        self._render_skilling()
        self._render_kingdom()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        for thread in (self._thread, self._account_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2_000)
        super().closeEvent(event)


_JOURNAL_STATUS_ORDER: dict[str, int] = {
    "Pending buy": 0,
    "Bought": 1,
    "Listed for sale": 2,
    "Partially sold": 3,
    "Completed": 4,
    "Completed (manual)": 4,
    "Cancelled": 5,
}


def _setting_int(key: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read an int QSetting, falling back to the default on a corrupted value."""
    try:
        value = int(QSettings().value(key, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def _gp(value: int) -> str:
    return f"{value:,} gp"


def _signed_gp(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,} gp"


def _leading_number(value: str) -> float:
    """Extract a formatted result's leading number for sign-aware coloring."""
    token = value.strip().removeprefix("Est. ").split(" ", 1)[0].replace(",", "")
    token = token.removesuffix("%")
    try:
        return float(token)
    except ValueError:
        return 0.0


def _availability(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Yes" if value else "No"


def _short_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} sec"
    if seconds < 3_600:
        return f"{seconds // 60} min"
    return f"{seconds // 3_600} hr"


def _item_detail_lines(items: tuple[SyncedItem, ...]) -> list[str]:
    if not items:
        return ["  Nothing"]
    lines: list[str] = []
    for item in items:
        if item.item_id == 995:
            lines.append(f"  {_gp(item.quantity)}")
        else:
            lines.append(
                f"  {item.quantity:,} × {item.item_name} "
                f"(estimated {_gp(item.total_value)})"
            )
    return lines


def _compact_items(items: tuple[SyncedItem, ...]) -> str:
    labels = [
        _gp(item.quantity)
        if item.item_id == 995
        else f"{item.quantity:,} × {item.item_name}"
        for item in items
    ]
    if len(labels) <= 2:
        return ", ".join(labels) if labels else "Nothing"
    return f"{', '.join(labels[:2])} +{len(labels) - 2} more"


def _synced_trade_label(trade: SyncedTrade) -> str:
    if trade.event_type == "player_trade":
        return f"With {trade.counterparty}" if trade.counterparty else "Player trade"
    items = trade.received if trade.direction == "buy" else trade.given
    item = next((entry for entry in items if entry.item_id != 995), None)
    action = "Bought" if trade.direction == "buy" else "Sold"
    return f"{action} {item.item_name}" if item else action


def _display_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def _table_sort_value(value: str) -> tuple[int, float | str]:
    cleaned = value.strip()
    if cleaned == "—":
        return (0, -1.0)
    tokens = cleaned.removeprefix("Est. ").split()
    if not tokens:
        return (1, "")
    numeric = tokens[0].removesuffix("%").replace(",", "")
    try:
        number = float(numeric)
    except ValueError:
        return (1, cleaned.casefold())
    if len(tokens) > 1 and tokens[1] in {"sec", "min", "hr"}:
        unit_seconds = {"sec": 1, "min": 60, "hr": 3_600}
        number *= unit_seconds[tokens[1]]
    return (0, number)


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
