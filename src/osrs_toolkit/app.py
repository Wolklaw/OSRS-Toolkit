from __future__ import annotations

import ctypes
import html
import os
import shutil
import sys
from collections.abc import Callable, Hashable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, NamedTuple

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QSettings,
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from osrs_toolkit import __version__
from osrs_toolkit.account import AccountLookupError, PlayerProfile, fetch_player
from osrs_toolkit.attention import (
    READY_TO_SELL_STATUS,
    journal_alert_positions,
    newly_reached,
)
from osrs_toolkit.buy_limits import BUY_LIMIT_WINDOW, buy_limit_status
from osrs_toolkit.calculators import (
    ALCH_POLICIES,
    SKILL_METHODS,
    alch_candidates,
    skill_results,
)
from osrs_toolkit.csv_export import journal_csv
from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade, TrackedTrade
from osrs_toolkit.journal_presentation import (
    JOURNAL_STATUS_FILTERS,
    PERIOD_FILTERS,
    PLANNED_STATUS,
    JournalPLPresentation,
    journal_display_status,
    journal_pl_presentation,
    journal_status_matches,
    tracked_position_within_period,
    trade_needs_attention,
    trade_within_period,
)
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.performance import (
    CalibrationRow,
    GroupPerformance,
    by_item,
    by_strategy,
    calibration,
    realized_results,
    summarize,
)
from osrs_toolkit.pvm import assess_all, estimate_gp_per_hour
from osrs_toolkit.ranking import (
    STRATEGIES,
    ge_tax,
    offer_targets,
    plan_flip_portfolio,
    rank_flips,
)
from osrs_toolkit.release_notes import (
    ReleaseNotes,
    catch_up_notes,
    catch_up_to_html,
    load_release_notes,
)
from osrs_toolkit.runelite_sync import (
    GE_SLOT_COUNT,
    TERMINAL_OFFER_STATES,
    GEOfferSlot,
    RuneLiteSyncImporter,
    ge_offer_status_label,
)
from osrs_toolkit.savings_goal import (
    SavingsProgress,
    daily_profit_rate,
    estimate_days_remaining,
    realized_profit_since,
)
from osrs_toolkit.supplies_report import supplies_spend_rows, total_supplies_spend
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
        # Search sits in a QHBoxLayout with a stretch factor next to fixed-width
        # spinboxes/combos, so without a floor it's the widget that gets squeezed
        # first when the window shrinks toward its minimum size.
        self.setMinimumWidth(180)

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


CHANGELOG_URL = "https://github.com/Wolklaw/OSRS-Toolkit/blob/main/CHANGELOG.md"

# No column may claim more than this from content measurement alone. Sized so a full item
# name still fits, while a sentence-long notes column has to elide instead of taking the
# width every column after it needs to stay on screen.
DEFAULT_MAXIMUM_COLUMN_WIDTH = 320

# A column that asked for less than this is holding a short label or a single figure, and
# has nothing to give back: squeezing it only turns "1,900,000 gp" into "1,900,000 …" while
# saving too little to matter. The wider columns hold the prose that can afford to shrink.
NARROW_COLUMN_WIDTH = 160

# The stylesheet's per-cell padding (7px a side), plus a little room so a column sized to
# its widest value does not elide that very value.
CELL_PADDING_WIDTH = 24
#: Where a confidence score stops being thin, and where it becomes a strong one. Thirds of
#: the room between the strategy's own minimum and 100 — see ``confidence_standing``.
_CONFIDENCE_FAIR = 1 / 3
_CONFIDENCE_STRONG = 2 / 3

# Which journal row a table row stands for, kept apart from column 0's UserRole because
# that one holds either a tracked position id or a manual entry's trade id, and the two
# are numbered from different tables. Only tracked rows carry this.
_FLASH_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
#: Positions with goods actually on the Grand Exchange, so a live offer's price is theirs.
_LISTED_STATUSES = frozenset({"Listed for sale", "Partially sold"})

# The Trade Journal's first tab. The doubled ampersand is Qt's escape for a literal one
# in a tab title; a single & would underline the C and take Alt+C.
_PLANS_TAB_TITLE = "Plans && completed"
_PLANS_TAB_INDEX = 0

#: The Guide column on the Skilling Profit and PvM Readiness tables — the one cell in each
#: row carrying a wiki URL, and so the only one a double-click opens.
_SKILL_GUIDE_COLUMN = 11
_PVM_GUIDE_COLUMN = 6

LAST_SEEN_VERSION_KEY = "app/last_seen_version"
SKIPPED_VERSION_KEY = "updates/skipped_version"
#: Where the window was last left. This app is opened beside a game and alt-tabbed to all
#: evening; putting it back where the player parked it is the difference between a tool and
#: a window you rearrange every launch.
WINDOW_GEOMETRY_KEY = "window/geometry"

# How much of the window has to land on a monitor that is actually attached for a restored
# geometry to be worth keeping: enough to see and to grab. Qt clamps most of these back on
# screen by itself — this is the sanity check for the docks and monitor arrangements where
# it doesn't.
_ONSCREEN_MINIMUM = 120


def current_release_notes(*, since: str = "", limit: int = 5) -> list[ReleaseNotes]:
    """What this build has to announce: the running version, plus anything released
    between ``since`` and it — an update that skips versions skips their notes too."""
    releases = load_release_notes(_resource_path("CHANGELOG.md"))
    return catch_up_notes(releases, current=__version__, since=since, limit=limit)


class WhatsNewDialog(QDialog):
    """The headlines for the version now running and any missed before it, read from the
    bundled changelog. Detail is a click away rather than on the page: someone catching up
    on three releases is here to find out what changed, not to read every entry in full."""

    def __init__(self, releases: list[ReleaseNotes], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"What's new in {releases[0].version}")
        self.setMinimumSize(640, 480)
        layout = QVBoxLayout(self)
        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(catch_up_to_html(releases))
        layout.addWidget(body)
        footer = QHBoxLayout()
        changelog_button = QPushButton("Full changelog", objectName="secondary")
        changelog_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(CHANGELOG_URL))
        )
        footer.addWidget(changelog_button)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Continue")
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)


class UpdateAvailableDialog(QDialog):
    """Offers a newer official release: install it now, defer it, or skip the version.

    Owns the download so both the start-up check and the manual check in Settings
    install an update the same way.
    """

    def __init__(
        self,
        release: ReleaseInfo,
        parent: QWidget | None = None,
        *,
        allow_skip: bool = True,
    ) -> None:
        super().__init__(parent)
        self.release = release
        self._download_thread: QThread | None = None
        self._download_worker: QObject | None = None
        self.setWindowTitle("Update available")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>Version {release.version} is available</h2>"))
        summary = QLabel(
            f"You are running version {__version__}. The installer is downloaded from the "
            "official GitHub release and its SHA-256 digest is verified before it opens."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        note = QLabel(
            "Save any work first — this app closes when the installer opens. If you use the "
            "portable edition, this creates a standard installed copy and leaves your portable "
            "folder unchanged.",
            objectName="muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel(objectName="recommendation")
        self.status.setWordWrap(True)
        # Styled as a panel, so it stays out of the layout until there is progress to report.
        self.status.hide()
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        self.notes_button = QPushButton("View release notes", objectName="secondary")
        self.notes_button.setEnabled(bool(release.page_url))
        self.notes_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(release.page_url))
        )
        actions.addWidget(self.notes_button)
        actions.addStretch()
        self.skip_button = QPushButton("Skip this version", objectName="secondary")
        self.skip_button.clicked.connect(self._skip_version)
        if allow_skip:
            actions.addWidget(self.skip_button)
        else:
            self.skip_button.hide()
        self.later_button = QPushButton("Remind me later", objectName="secondary")
        self.later_button.clicked.connect(self.reject)
        actions.addWidget(self.later_button)
        self.install_button = QPushButton(f"Download and install {release.version}")
        self.install_button.clicked.connect(self._start_download)
        actions.addWidget(self.install_button)
        layout.addLayout(actions)

    def _skip_version(self) -> None:
        QSettings().setValue(SKIPPED_VERSION_KEY, self.release.version)
        self.reject()

    def _report(self, message: str) -> None:
        self.status.setText(message)
        self.status.show()

    def _set_busy(self, busy: bool) -> None:
        for button in (self.install_button, self.skip_button, self.later_button):
            button.setEnabled(not busy)

    def _start_download(self) -> None:
        if self._download_thread is not None and self._download_thread.isRunning():
            return
        self._set_busy(True)
        self._report("Downloading update…")
        thread = QThread(self)
        worker = UpdateDownloadWorker(self.release)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(
            lambda value: self._report(f"Downloading update… {value}%")
        )
        worker.finished.connect(self._downloaded)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._clear_download_worker)
        self._download_thread = thread
        self._download_worker = worker
        thread.start()

    def _downloaded(self, path: Path) -> None:
        self._report("Verified. Opening the installer…")
        try:
            launch_installer(path)
        except Exception as exc:  # noqa: BLE001 - present launch failures to the user.
            self._download_failed(str(exc))
            return
        QApplication.quit()

    def _download_failed(self, message: str) -> None:
        self._report("The update could not be installed. You can try again in a moment.")
        self._set_busy(False)
        self.install_button.setText("Try again")
        QMessageBox.warning(self, "Update unavailable", message)

    def _clear_download_worker(self) -> None:
        if self._download_thread is not None:
            self._download_thread.deleteLater()
        self._download_worker = None
        self._download_thread = None

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # A download in flight writes to a temp file and verifies its digest; let it
        # finish rather than leaving a half-written installer behind.
        if self._download_thread is not None and self._download_thread.isRunning():
            self._report(
                "Finishing the download — this window closes when the installer opens."
            )
            event.ignore()
            return
        super().closeEvent(event)


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


class _Palette(NamedTuple):
    """One theme's colours, plus the few shape choices a theme is allowed to make.

    The fields carrying defaults hold what the themes already agreed on, so each theme
    spells out only what it changes.
    """

    background: str
    sidebar: str
    text: str
    muted: str
    field: str
    border: str
    header: str
    profit: str
    nav_selected_text: str
    selection_bg: str
    selection_text: str
    link: str
    disabled_bg: str
    disabled_text: str
    loss: str = "#ef6b73"
    pending: str = "#65a9ff"
    bought: str = "#d5ad52"
    listed: str = "#b49af5"
    partial: str = "#e7a35a"
    accent: str = "#d5ad52"
    accent_hover: str = "#e2bd64"
    on_accent: str = "#11151b"
    # The attention blink. ``flash`` is the edge and the lit-up text, ``flash_row`` the
    # wash laid behind text that keeps its own colour — a journal row's status and P/L
    # still have to be readable through it, so it is a tint rather than a fill.
    flash: str = "#ffd34d"
    flash_row: str = "#4a3c14"
    button_border: str = "0"
    focus: str = "#d5ad52"
    text_selection: str = "#765f2d"
    square_corners: bool = False
    icon_variant: str = ""


_PALETTES: dict[str, _Palette] = {
    "Dark": _Palette(
        background="#11151b",
        sidebar="#171c24",
        text="#e8edf3",
        muted="#91a0b4",
        field="#1a2029",
        border="#28313d",
        header="#1e252f",
        profit="#70d6a1",
        nav_selected_text="#d5ad52",
        selection_bg="#403a2c",
        selection_text="#e8edf3",
        link="#65a9ff",
        disabled_bg="#28313d",
        disabled_text="#91a0b4",
    ),
    "Midnight": _Palette(
        background="#08111f",
        sidebar="#0d192b",
        text="#e5efff",
        muted="#8fa5c4",
        field="#122139",
        border="#263b59",
        header="#172942",
        profit="#67d7c4",
        nav_selected_text="#d5ad52",
        selection_bg="#403a2c",
        selection_text="#e5efff",
        link="#65a9ff",
        disabled_bg="#263b59",
        disabled_text="#8fa5c4",
    ),
    "Light": _Palette(
        background="#f3f5f7",
        sidebar="#e7ebef",
        text="#1d2833",
        muted="#5e6b78",
        field="#ffffff",
        border="#cbd3dc",
        header="#dfe5eb",
        profit="#16845b",
        nav_selected_text="#8a6500",
        selection_bg="#dbe6f5",
        selection_text="#1d2833",
        link="#1f64a8",
        disabled_bg="#d6dbe0",
        disabled_text="#8a939c",
        loss="#d94752",
        pending="#1f64a8",
        bought="#8a6500",
        listed="#6b4fb3",
        partial="#a65300",
        flash="#a86a00",
        flash_row="#ffeeb0",
        icon_variant="-dark",
    ),
    # Dressed as the game's own interfaces: carved stone panels over a dark leather
    # canvas, square corners rather than rounded ones, and the interface orange on
    # whatever is meant to be read first. The numbers keep their own greens and reds — a
    # flip that lost money has to look like a loss before it looks like Gielinor.
    "Old School": _Palette(
        background="#241f18",
        sidebar="#2f2921",
        text="#f2e8d5",
        muted="#a89878",
        field="#3a3327",
        border="#5a4e39",
        header="#463e2d",
        profit="#5fd35f",
        nav_selected_text="#ff981f",
        selection_bg="#6b5a34",
        selection_text="#fff3d6",
        link="#7ab4ff",
        disabled_bg="#2c2720",
        disabled_text="#7d7159",
        loss="#f2564b",
        pending="#7ab4ff",
        bought="#ff981f",
        listed="#c0a2f2",
        partial="#e0a44f",
        accent="#c8912f",
        accent_hover="#dda63c",
        on_accent="#241f18",
        flash="#ff981f",
        flash_row="#5c4318",
        button_border="1px solid #1b1710",
        focus="#ff981f",
        text_selection="#6b5a34",
        square_corners=True,
    ),
}


class SettingsDialog(QDialog):
    THEMES: ClassVar[list[str]] = list(_PALETTES)

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
            "terms of Jagex's Fan Content Policy. This content is not endorsed by or "
            "affiliated with Jagex.</b></p>"
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
        about_actions = QHBoxLayout()
        self.whats_new_button = QPushButton("What's new", objectName="secondary")
        self.whats_new_button.clicked.connect(self._show_whats_new)
        about_actions.addWidget(self.whats_new_button)
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self._update_action)
        about_actions.addWidget(self.update_button)
        about_layout.addLayout(about_actions)
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

    def _show_whats_new(self) -> None:
        # Opened deliberately from Settings rather than on an update, so it shows recent
        # history whether or not this launch had anything new to report.
        notes = current_release_notes()
        if not notes:
            QMessageBox.information(
                self,
                "What's new",
                f"Release notes for version {__version__} are not available in this build. "
                "The full changelog is on GitHub.",
            )
            return
        WhatsNewDialog(notes, self).exec()

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
        # A manual check is already a decision to look at this release, so the offer
        # window handles it from here; skipping only belongs to the start-up check.
        UpdateAvailableDialog(release, self, allow_skip=False).exec()

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


class SavingsGoalDialog(QDialog):
    def __init__(self, label: str, target: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Savings goal")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.goal_label = QLineEdit(label, placeholderText="e.g. Twisted bow")
        self.goal_target = TradeEntryDialog._number_field(1, 2_000_000_000, target or 1_000_000)
        self.goal_target.setSuffix(" gp")
        form.addRow("What for", self.goal_label)
        form.addRow("Target amount", self.goal_target)
        layout.addLayout(form)
        note = QLabel(
            "Progress only counts realized profit from when this goal starts — changing "
            "the label or target here doesn't reset it, only Clear goal does.",
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

    def _accept_if_valid(self) -> None:
        if not self.goal_label.text().strip():
            QMessageBox.warning(self, "Missing label", "Enter what you're saving for.")
            return
        self.accept()


class UpdateTrackedTradeDialog(QDialog):
    STATUSES: ClassVar[list[str]] = [
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Cancelled",
        "Supplies",
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


class ClickableCard(QLabel):
    """A summary card that will show you the rows behind its own number.

    A count with no way to reach what it counts is a dead end: "Needs attention 2" over a
    journal of fifty rows leaves the player to find the two by eye, which is the work the
    card was supposed to save. It only offers itself while it has something to point at —
    the hand cursor is the whole affordance, so a card reading zero must not show one.
    """

    clicked = Signal()

    def __init__(self, text: str, **kwargs: object) -> None:
        super().__init__(text, **kwargs)
        self._live = False

    def set_live(self, live: bool) -> None:
        if live == self._live:
            return
        self._live = live
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if live else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            self._live
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ResponsiveTableWidget(QTableWidget):
    """Fill wide viewports while preserving readable widths in compact windows.

    Each column has a preferred width. Spare viewport space is shared out in proportion to
    those widths, and a viewport too narrow for them scales them all back down the same
    way, each stopping at a floor below which it has nothing useful left to show. Fitting
    the viewport wins over any single column's ideal width: a column pushed past the right
    edge is one the user has no reason to believe exists at all.
    """

    #: A row opened from the keyboard, as ``(row, column)`` to match ``cellDoubleClicked``.
    rowActivated = Signal(int, int)

    def __init__(self, column_count: int) -> None:
        super().__init__(0, column_count)
        self._preferred_widths: list[int] = []
        self._floor_widths: list[int] = []
        self._applying_widths = False
        self._user_resized_columns: set[int] = set()
        self._tooltip_index = QModelIndex()
        self.horizontalHeader().sectionResized.connect(self._section_resized)
        # Qt's native tooltip only fires after the cursor stops moving for a moment, over
        # whichever cell it happens to land on — for a single narrow glyph like the ⚠ in an
        # item name, that is a small target to hold still on, and it was the whole
        # complaint this exists to answer. Tracking every move and showing on arrival, not
        # on stillness, turns "hold the mouse on this exact spot" into "pass over the row".
        self.setMouseTracking(True)

    def _section_resized(self, column: int, _old_width: int, _new_width: int) -> None:
        if not self._applying_widths:
            self._user_resized_columns.add(column)

    def viewportEvent(self, event) -> bool:  # type: ignore[no-untyped-def]
        """Take over tooltips entirely; ``mouseMoveEvent`` is what actually shows them.

        Left to Qt, a tooltip request only arrives once the cursor has already stopped
        moving, which is the delay ``mouseMoveEvent`` exists to remove. Letting this event
        through as well would just show the same text a second time on a timer.
        """
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().mouseMoveEvent(event)
        index = self.indexAt(event.position().toPoint())
        if index == self._tooltip_index:
            return
        self._tooltip_index = index
        item = self.itemFromIndex(index) if index.isValid() else None
        text = self._tooltip_text(item, index) if item is not None else ""
        if not text:
            QToolTip.hideText()
            return
        # Anchored to the cell's own rect, which is what lets Qt hide the tooltip the
        # instant the cursor actually leaves the row instead of the next stray move event.
        QToolTip.showText(event.globalPosition().toPoint(), text, self, self.visualRect(index))

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().leaveEvent(event)
        self._tooltip_index = QModelIndex()
        QToolTip.hideText()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Enter opens the current row, the way double-clicking it does.

        Qt's own ``activated`` signal already means "open this", but on Windows it fires on
        the double-click that ``cellDoubleClicked`` reports as well — one gesture, two
        dialogs. A signal of its own keeps the mouse and the keyboard on separate wires, so
        each opens a row exactly once.
        """
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers()
            and self.currentIndex().isValid()
        ):
            index = self.currentIndex()
            self.rowActivated.emit(index.row(), index.column())
            event.accept()
            return
        super().keyPressEvent(event)

    def _tooltip_text(self, item: QTableWidgetItem, index) -> str:  # type: ignore[no-untyped-def]
        """A cell's tooltip, minus the one case that only repeats what is already on screen.

        ``_fill_table`` gives every cell its text as a tooltip so an elided figure stays
        readable. On a cell that already fits, that is a box popping up to say what the
        user is looking at — harmless while tooltips were unstyled and easy to miss, and
        plain noise once they were themed into something that looks like the app talking.

        Only that exact repetition is dropped. A tooltip whose text differs from the cell
        is somebody's deliberate explanation — a P/L breakdown, a stale ask, why a row reads
        "Planned" — and always shows.
        """
        tooltip = item.toolTip()
        if tooltip != item.text() or self._is_elided(item, index):
            return tooltip
        return ""

    def _is_elided(self, item: QTableWidgetItem, index) -> bool:  # type: ignore[no-untyped-def]
        """Whether this cell is showing less than its whole text."""
        width = self.visualRect(index).width() - CELL_PADDING_WIDTH
        return QFontMetrics(item.font()).horizontalAdvance(item.text()) > width

    def begin_bulk_resize(self) -> None:
        """Suspend user-resize tracking around programmatic sizing (e.g. initial layout)."""
        self._applying_widths = True

    def end_bulk_resize(self) -> None:
        self._applying_widths = False

    def set_preferred_widths(self, widths: list[int]) -> None:
        self._preferred_widths = widths
        self._floor_widths = [
            min(width, max(self._header_floor(column), NARROW_COLUMN_WIDTH))
            for column, width in enumerate(widths)
        ]
        self._apply_responsive_widths()

    def _header_floor(self, column: int) -> int:
        """The narrowest this column may be squeezed to: enough to read its own header."""
        header = self.horizontalHeader()
        item = self.horizontalHeaderItem(column)
        label = item.text() if item is not None else ""
        # The stylesheet pads header sections by 10px a side, and a sorted column also
        # gives up room to its indicator.
        return max(
            header.minimumSectionSize(),
            header.fontMetrics().horizontalAdvance(label) + 40,
        )

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
            if preferred_total <= 0:
                return
            available = max(0, self.viewport().width() - 2 - locked_width)
            widths = (
                self._grown_widths(flexible_columns, available - preferred_total)
                if available >= preferred_total
                else self._shrunk_widths(flexible_columns, available)
            )
            for column in flexible_columns:
                self.setColumnWidth(column, widths[column])
        finally:
            self._applying_widths = False

    def _grown_widths(self, columns: list[int], extra: int) -> dict[int, int]:
        """Share spare viewport space out in proportion to each column's preferred width."""
        weights = [self._preferred_widths[column] for column in columns]
        additions = self._distribute(columns, extra, weights)
        return {column: self._preferred_widths[column] + additions[column] for column in columns}

    def _shrunk_widths(self, columns: list[int], available: int) -> dict[int, int]:
        """Scale every column down together, holding each at its floor.

        Shrinking in proportion keeps the wide-window layout recognizable: the column that
        reads as the widest one on a large monitor is still the widest on a small one.
        Taking the width only from the columns with the most slack would instead collapse
        the long item-name column that identifies each row while short numeric ones kept
        every pixel. Columns that would fall under their floor are pinned there and the
        rest re-share what is left, which can leave the floors alone overflowing a very
        narrow viewport — that is the point at which the table has to scroll.
        """
        widths = {column: self._floor_widths[column] for column in columns}
        scalable = columns
        budget = available
        while scalable:
            preferred = [self._preferred_widths[column] for column in scalable]
            preferred_total = sum(preferred)
            if preferred_total <= 0:
                break
            scale = budget / preferred_total
            pinned = {
                column
                for column in scalable
                if self._preferred_widths[column] * scale < self._floor_widths[column]
            }
            if not pinned:
                widths.update(self._distribute(scalable, budget, preferred))
                break
            budget -= sum(self._floor_widths[column] for column in pinned)
            scalable = [column for column in scalable if column not in pinned]
        return widths

    @staticmethod
    def _distribute(columns: list[int], amount: int, weights: list[int]) -> dict[int, int]:
        """Split ``amount`` across ``columns`` in proportion to ``weights``.

        Rounding each share independently leaves a few pixels over; the column with the
        most weight absorbs them, so the parts always add back up to ``amount`` exactly and
        the widest column is the one that can afford the correction.
        """
        total = sum(weights)
        if total <= 0:
            return dict.fromkeys(columns, 0)
        shares = {
            column: round(amount * weight / total)
            for column, weight in zip(columns, weights, strict=True)
        }
        widest = columns[max(range(len(columns)), key=lambda index: weights[index])]
        shares[widest] += amount - sum(shares.values())
        return shares


class ElidedLabel(QLabel):
    """A label that shortens its own text to whatever width the layout gives it.

    A plain QLabel refuses to go below the width of its text, so one long item name would
    stretch its column and unbalance the whole grid. This one keeps the full text for the
    tooltip and shows as much of it as fits.
    """

    def __init__(self, object_name: str) -> None:
        super().__init__(objectName=object_name)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setFullText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._apply_elision()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
            )
        )


class FittedScrollArea(QScrollArea):
    """A scroll area that takes exactly the height its contents want, up to a ceiling.

    The GE Flipper's recommendation runs from one row to eight, so a fixed height either
    padded a short plan with dead space or hid most of a long one behind a scrollbar. This
    asks the contents how tall they want to be at the width they were given and grows to
    match. It gives way once the window is too short to hold both: ``window_reserve`` is
    the height the rest of the page needs — everything around it, plus enough table to
    still be worth showing — and whatever is left over is all this may take.
    """

    def __init__(self, content: QWidget, *, minimum: int, window_reserve: int) -> None:
        super().__init__()
        self._content = content
        self._minimum = minimum
        self._window_reserve = window_reserve
        self._applied_height = -1
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        """Re-measure the contents. Call after changing them, and on a window resize."""
        # Measured as if the vertical scrollbar were always there: assuming the narrower
        # width can only over-estimate the height, whereas assuming the wider one would let
        # a scrollbar appear, re-wrap the text taller, and need the scrollbar all over
        # again — a resize loop.
        width = self.width() - self.verticalScrollBar().sizeHint().width()
        wanted = self._content.heightForWidth(width) if width > 0 else 0
        if wanted <= 0:
            wanted = self._content.sizeHint().height()
        ceiling = max(self._minimum, self.window().height() - self._window_reserve)
        height = max(0, min(wanted, ceiling))
        if height != self._applied_height:
            self._applied_height = height
            self.setFixedHeight(height)


class AttentionFlasher(QObject):
    """Blinks a set of things on and off a few times, to put the eye on what just changed.

    It keeps only the bookkeeping — which things are lit, and whether this instant is an
    "on" beat — and leaves the drawing to whoever connected to ``pulsed``, because a table
    row and a Grand Exchange slot card are highlighted in quite different ways. A key is
    whatever the caller identifies its targets by: a row's position id, a slot's index.
    """

    pulsed = Signal()

    #: Three full blinks, ending dark. Long enough to catch someone looking back at the
    #: window, short enough to be over before they start reading the row it pointed at.
    BEATS = 6
    BEAT_MS = 260

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keys: set[Hashable] = set()
        self._beats_left = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.BEAT_MS)
        self._timer.timeout.connect(self._beat)

    def start(self, keys: Iterable[Hashable]) -> None:
        """Blink ``keys``, restarting the count and keeping anything already blinking.

        Two offers finishing a second apart is ordinary, and the second must not cut the
        first one's blink short — so they join it and the whole set runs the full length.
        """
        added = set(keys)
        if not added:
            return
        self._keys |= added
        self._beats_left = self.BEATS
        self._timer.start()
        self.pulsed.emit()

    def focus(self, keys: Iterable[Hashable]) -> None:
        """Blink ``keys`` and only ``keys``, dropping whatever was blinking before.

        ``start`` joins, because two offers finishing seconds apart are two things that both
        happened and both deserve their blink. A player clicking a slot card is not a second
        event to add to the first: it is one question — "which row is this?" — asked again in
        place of the last one. Joining there answered it with every row the player had asked
        about since, so clicking the second slot blinked the first slot's row alongside it,
        and clicking along the whole Grand Exchange washed the entire table amber.
        """
        wanted = set(keys)
        if not wanted:
            return
        self._keys = wanted
        self._beats_left = self.BEATS
        self._timer.start()
        self.pulsed.emit()

    def stop(self) -> None:
        self._timer.stop()
        self._keys.clear()
        self._beats_left = 0

    def is_lit(self, key: Hashable) -> bool:
        """Whether ``key`` should be drawn highlighted at this instant."""
        return self._beats_left > 0 and self._beats_left % 2 == 0 and key in self._keys

    def _beat(self) -> None:
        self._beats_left -= 1
        if self._beats_left <= 0:
            self.stop()
        # Emitted after stopping too, so the last beat is what clears the highlight rather
        # than leaving whatever the previous one painted on screen for good.
        self.pulsed.emit()


class GEOfferSlotCard(QFrame):
    """One Grand Exchange slot, laid out like the slot it mirrors in-game.

    The same figures the old GE Offers table listed as text, read as a filling bar instead:
    "31 / 116" tells you where an offer stands only after you divide it, which is the one
    thing a glance at the real Grand Exchange never asks you to do.
    """

    #: The item this slot is holding an offer for, so the page can go and find its journal
    #: row. Zero — an empty slot — is never emitted.
    clicked = Signal(int)

    def __init__(self, slot_index: int) -> None:
        super().__init__(objectName="geSlot")
        self._slot_index = slot_index
        self._item_id = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._number = QLabel(f"{slot_index + 1}", objectName="geSlotNumber")
        self._status = QLabel(objectName="geSlotStatus")
        top.addWidget(self._number)
        top.addStretch()
        top.addWidget(self._status)
        layout.addLayout(top)

        self._item = ElidedLabel("geSlotItem")
        layout.addWidget(self._item)
        self._price = QLabel(objectName="geSlotPrice")
        layout.addWidget(self._price)
        layout.addStretch()

        self._progress = QProgressBar(objectName="geSlotProgress")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        layout.addWidget(self._progress)
        self._filled = QLabel(objectName="geSlotPrice")
        self._filled.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._filled)

    def show_empty(self) -> None:
        self.setProperty("slotState", "empty")
        self._item_id = 0
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._item.setFullText("Empty")
        self._status.setText("")
        self._price.setText("")
        self._filled.setText("")
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self.setToolTip("This Grand Exchange slot is free.")
        self._repolish()

    def show_offer(self, slot: GEOfferSlot) -> None:
        side = "Buy" if slot.side == "buy" else "Sell" if slot.side == "sell" else ""
        # Terminal offers are done but still sitting in the slot until collected in-game,
        # which is the state worth spotting from across the room — so it gets its own tone
        # rather than reading as just another offer in progress.
        self.setProperty(
            "slotState", "collect" if slot.is_terminal else slot.side or "empty"
        )
        self._item_id = slot.item_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._number.setText(f"{self._slot_index + 1}  {side}".strip())
        self._status.setText(ge_offer_status_label(slot.state))
        self._item.setFullText(slot.item_name)
        self._price.setText(f"{_gp(slot.offer_price)} ea.")
        self._filled.setText(
            f"{slot.quantity_filled:,} / {slot.total_quantity:,} · {slot.percent_filled:.0f}%"
        )
        self._progress.setVisible(True)
        self._progress.setValue(round(slot.percent_filled))
        self.setToolTip(
            f"Slot {self._slot_index + 1}: {ge_offer_status_label(slot.state)} "
            f"{slot.total_quantity:,} × {slot.item_name} at {_gp(slot.offer_price)} each.\n"
            f"{slot.quantity_filled:,} filled so far, {_gp(slot.spent_gp)} moved.\n"
            "Click to find this item's row in the journal below."
        )
        self._repolish()

    def set_flashing(self, lit: bool) -> None:
        """Light the whole card up, or put it back to the tone its state calls for."""
        wanted = "on" if lit else ""
        if self.property("flash") == wanted:
            return
        self.setProperty("flash", wanted)
        self._repolish()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._item_id > 0
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit(self._item_id)

    def _repolish(self) -> None:
        """Qt only re-reads a property-based stylesheet rule when asked to."""
        for widget in (
            self,
            self._number,
            self._status,
            self._item,
            self._price,
            self._filled,
            self._progress,
        ):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class MainWindow(QMainWindow):
    NAV_ITEMS: ClassVar[list[str]] = [
        "GE Flipper",
        "Watchlist",
        "Trade Journal",
        "Performance",
        "Alch Finder",
        "Skilling Profit",
        "PvM Readiness",
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
        self._update_thread: QThread | None = None
        self._update_worker: QObject | None = None
        self._account_lookup_name = ""
        self._account_lookup_automatic = False
        self._failed_auto_accounts: set[str] = set()
        self._profile: PlayerProfile | None = None
        self._mappings: dict[int, ItemMapping] = {}
        self._points: list[MarketPoint] = []
        self._flips: list[FlipCandidate] = []
        self._portfolio: list[FlipCandidate] = []
        self._excluded_item_ids: set[int] = set()
        self._synced_trade_rows: dict[str, tuple[SyncedTrade, tuple[str, ...]]] = {}
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
        # Dedicated to on-demand item-details lookups (price history), separate from the
        # transient client MarketWorker creates for each periodic snapshot poll.
        self._market_client = WikiMarketClient()
        self._loadout_snapshot = self._journal.get_latest_loadout_snapshot()
        self._last_sync_message = ""
        self._profit_color = "#70d6a1"
        self._loss_color = "#ef6b73"
        self._muted_color = "#91a0b4"
        self._link_color = "#65a9ff"
        self._warning_color = "#d5ad52"
        self._journal_status_colors = {
            "Planned": "#8a8f98",
            "Pending buy": "#65a9ff",
            "Bought": "#d5ad52",
            "Listed for sale": "#b49af5",
            "Partially sold": "#e7a35a",
            "Completed": self._profit_color,
            "Completed (manual)": self._profit_color,
            "Cancelled": self._muted_color,
            "Supplies": self._muted_color,
        }
        self._flash_color = "#ffd34d"
        self._flash_row_color = "#4a3c14"
        # The last state each renderer saw, so the next pass can tell what has just
        # changed. None means "nothing seen yet": the first look after start-up seeds
        # these and announces nothing, or every offer left finished overnight would blink
        # for attention the moment the app opened.
        self._journal_statuses: dict[int, str] | None = None
        self._ge_slot_states: dict[int, str] | None = None
        #: What the Needs attention card is counting, so clicking it can go and show them.
        self._attention_positions: set[int] = set()
        # Held until the surface each points at is actually being looked at — see
        # _release_pending_flashes.
        self._pending_journal_flash: set[int] = set()
        self._pending_slot_flash: set[int] = set()
        self._journal_flasher = AttentionFlasher(self)
        self._journal_flasher.pulsed.connect(self._paint_journal_flash)
        self._slot_flasher = AttentionFlasher(self)
        self._slot_flasher.pulsed.connect(self._paint_slot_flash)
        self._market_buttons: list[QPushButton] = []
        # Buttons that act on "the selected row". Held here so selection changes can switch
        # them on and off together: a button that is always enabled and answers an empty
        # table with "select a row first" is a dead end where a greyed-out button would
        # have said the same thing before the click.
        self._journal_row_buttons: list[QPushButton] = []
        self._activity_row_buttons: list[QPushButton] = []
        # The Journal page is built first and renders itself as it is built, which reaches
        # the Performance page's renderer before its widgets exist.
        self._performance_ready = False
        self._cash_debounce = QTimer(self)
        self._cash_debounce.setSingleShot(True)
        self._cash_debounce.setInterval(200)
        self._cash_debounce.timeout.connect(self._cash_changed)
        self._build_ui()
        self._apply_theme(self._theme)
        self._restore_window_geometry()
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(3_000)
        self._sync_timer.timeout.connect(self._import_runelite_events)
        self._sync_timer.start()
        # Each of these passes ``self`` as the receiver so Qt drops the pending call if the
        # window is gone by the time it comes due. Without it, closing the app inside the
        # first second leaves a callback aimed at a half-destroyed window.
        QTimer.singleShot(100, self, self._import_runelite_events)
        QTimer.singleShot(250, self, self.load_market)
        # Let the window paint before anything modal appears in front of it.
        QTimer.singleShot(600, self, self._run_startup_notices)
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
        # Ctrl+1..7, in sidebar order. Someone alt-tabbing in from the game to check one
        # page and going straight back should not have to find it with the mouse first;
        # the tooltip on each row is where the shortcut is advertised.
        for index, title in enumerate(self.NAV_ITEMS):
            self.nav.item(index).setToolTip(f"{title}  (Ctrl+{index + 1})")
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda row=index: self.nav.setCurrentRow(row))
        # load_market already ignores a refresh asked for while one is running.
        QShortcut(QKeySequence.StandardKey.Refresh, self).activated.connect(self.load_market)
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
        self.pages.addWidget(self._build_performance_page())
        self.pages.addWidget(self._build_alch_page())
        self.pages.addWidget(self._build_skilling_page())
        self.pages.addWidget(self._build_pvm_page())
        layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)

    def _restore_window_geometry(self) -> None:
        """Reopen where the player left the window, when that place still exists.

        ``restoreGeometry`` does its own clamping first — a frame saved on a monitor that
        has since been unplugged comes back onto an attached one — so the check below is a
        backstop, not the main safeguard, for the arrangements where that clamp picks the
        wrong screen or none. ``__init__``'s own ``resize`` is what it falls back to: the
        wrong size is recoverable in a way a window nobody can reach is not.
        """
        saved = QSettings().value(WINDOW_GEOMETRY_KEY)
        if not isinstance(saved, (QByteArray, bytes, bytearray)):
            return
        default_geometry = self.geometry()
        if not self.restoreGeometry(QByteArray(saved)):
            return
        if not self._is_on_an_attached_screen():
            self.setGeometry(default_geometry)

    def _is_on_an_attached_screen(self) -> bool:
        """Whether enough of the window has landed somewhere the player can see and grab."""
        frame = self.frameGeometry()
        for screen in QGuiApplication.screens():
            visible = screen.availableGeometry().intersected(frame)
            if visible.width() >= _ONSCREEN_MINIMUM and visible.height() >= _ONSCREEN_MINIMUM:
                return True
        return False

    def _open_rows_with(
        self, table: ResponsiveTableWidget, open_row: Callable[[int, int], None]
    ) -> None:
        """Wire both ways of opening a row: double-click it, or select it and press Enter.

        Every table in this app had only the first, which left the keyboard able to walk a
        table and read it but never able to open anything in it — on pages the Ctrl+number
        shortcuts exist to reach without touching the mouse at all.
        """
        table.cellDoubleClicked.connect(open_row)
        table.rowActivated.connect(open_row)

    def _install_row_menu(
        self, table: ResponsiveTableWidget, build: Callable[[QMenu, int], None]
    ) -> None:
        """Right-click a row for the verbs that apply to it.

        ``build`` is handed an empty menu and the row the click landed on. That row is made
        current first, so an action reading "the selected trade" always means the row the
        menu opened over, and an empty menu is never shown at all.
        """
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def show_menu(position) -> None:  # type: ignore[no-untyped-def]
            index = table.indexAt(position)
            if not index.isValid():
                return
            table.setCurrentCell(index.row(), index.column())
            menu = QMenu(table)
            build(menu, index.row())
            if menu.isEmpty():
                return
            menu.exec(table.viewport().mapToGlobal(position))

        table.customContextMenuRequested.connect(show_menu)

    def _row_item_id(self, table: QTableWidget, row: int) -> int | None:
        """The item a row stands for, as ``_fill_table`` stored it against column 0."""
        anchor = table.item(row, 0)
        if anchor is None:
            return None
        item_id = anchor.data(Qt.ItemDataRole.UserRole)
        return item_id if isinstance(item_id, int) else None

    def _copy_text(self, text: str) -> None:
        """Put one field on the clipboard, ready to paste into the game's own search box."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _add_copy_action(self, menu: QMenu, table: QTableWidget, row: int, column: int) -> None:
        """A "copy this name" entry, when the row has a name to copy.

        The Grand Exchange search box is the other end of nearly every row here, and typing
        "Zulrah's scales" by hand into it is the one step this app could never help with.
        """
        cell = table.item(row, column)
        if cell is None or not cell.text().strip():
            return
        name = cell.text()
        menu.addAction(f"Copy “{name}”", lambda: self._copy_text(name))

    def _set_watched(self, item_id: int, watched: bool) -> None:
        """Add or drop a watchlist entry and persist it, from wherever the ask came from."""
        if watched:
            self._watchlist.add(item_id)
        else:
            self._watchlist.discard(item_id)
        QSettings().setValue("market/watchlist", sorted(self._watchlist))
        self._render_watchlist()

    def _build_market_row_menu(self, table: ResponsiveTableWidget) -> Callable[[QMenu, int], None]:
        """The row menu for a table of market items: details, tracking, watchlist, copy.

        Shared by the GE Flipper, the Watchlist and the Alch Finder, which all key their
        rows to an item id and all already answer a double-click with the same dialog.
        """

        def build(menu: QMenu, row: int) -> None:
            item_id = self._row_item_id(table, row)
            if item_id is None:
                self._add_copy_action(menu, table, row, 0)
                return
            menu.addAction("View details…", lambda: self._open_market_item(table, row))
            flip = next(
                (candidate for candidate in self._flips if candidate.item_id == item_id), None
            )
            if flip is not None:
                menu.addAction("Track this flip", lambda: self._track_candidate(flip))
            watched = item_id in self._watchlist
            menu.addAction(
                "Remove from watchlist" if watched else "Add to watchlist",
                lambda: self._set_watched(item_id, not watched),
            )
            menu.addSeparator()
            self._add_copy_action(menu, table, row, 0)

        return build

    def _delete_selected_row_on_delete_key(
        self, table: ResponsiveTableWidget, delete: Callable[[], None]
    ) -> None:
        """Delete removes the selected row, scoped to the table that has focus.

        Both handlers ask for confirmation before anything goes, so the key cannot lose a
        trade on its own — it only saves crossing the window to the button that was already
        there.
        """
        shortcut = QShortcut(QKeySequence.StandardKey.Delete, table)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(delete)

    def _journal_selection_changed(self) -> None:
        selected = bool(self.journal_table.selectionModel().hasSelection())
        for button in self._journal_row_buttons:
            button.setEnabled(selected)

    def _activity_selection_changed(self) -> None:
        selected = bool(self.synced_trade_table.selectionModel().hasSelection())
        for button in self._activity_row_buttons:
            button.setEnabled(selected)

    def _build_journal_row_menu(self, menu: QMenu, row: int) -> None:
        """The row menu for a journal entry: the two buttons above it, on the row itself."""
        menu.addAction("Update trade…", self._update_selected_trade)
        menu.addAction("Delete trade…", self._delete_selected_trade)
        menu.addSeparator()
        self._add_copy_action(menu, self.journal_table, row, 2)

    def _build_activity_row_menu(self, menu: QMenu, row: int) -> None:
        """The row menu for an imported RuneLite event."""
        menu.addAction("View details…", self._open_selected_synced_trade)
        menu.addAction("Delete entry…", self._delete_selected_synced_trade)
        menu.addSeparator()
        self._add_copy_action(menu, self.synced_trade_table, row, 3)

    def _build_guide_row_menu(
        self,
        table: ResponsiveTableWidget,
        guide_column: int,
        open_guide: Callable[[int, int], None],
    ) -> Callable[[QMenu, int], None]:
        """The row menu for a table whose rows link out to the wiki."""

        def build(menu: QMenu, row: int) -> None:
            cell = table.item(row, guide_column)
            if cell is not None and isinstance(cell.data(Qt.ItemDataRole.UserRole), str):
                menu.addAction("Open wiki guide", lambda: open_guide(row, guide_column))
                menu.addSeparator()
            self._add_copy_action(menu, table, row, 0)

        return build

    def _page_heading(
        self, title: str, subtitle: str, *, market_refresh: bool = True
    ) -> QVBoxLayout:
        """Standard page header. Pages that read no market data pass ``market_refresh=False``
        rather than offering a button that cannot change anything they show."""
        layout = QVBoxLayout()
        heading = QHBoxLayout()
        heading.addWidget(QLabel(title, objectName="title"))
        heading.addStretch()
        if market_refresh:
            refresh = QPushButton("Refresh market", objectName="refreshButton")
            refresh.setToolTip("Download the latest market snapshot for every page  (F5)")
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
        # The plan's headline and its closing note sit outside the scroll area so that a
        # long list scrolls under them instead of carrying them off the top of the card.
        recommendation = QFrame(objectName="recommendation")
        recommendation_layout = QVBoxLayout(recommendation)
        recommendation_layout.setSpacing(10)
        self.flip_recommendation_headline = QLabel("Waiting for market prices…")
        self.flip_recommendation_headline.setWordWrap(True)
        self.flip_recommendation = QLabel()
        self.flip_recommendation.setWordWrap(True)
        self.flip_recommendation.setTextFormat(Qt.TextFormat.RichText)
        self.flip_recommendation.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.flip_recommendation.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.flip_recommendation.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # The reserve covers this page's heading, controls, the card's own text, the
        # buttons, and roughly four rows of flip table. A full eight-offer plan clears it
        # from about 900px of window height and scrolls below that.
        self.flip_recommendation_rows = FittedScrollArea(
            self.flip_recommendation, minimum=64, window_reserve=580
        )
        self.flip_recommendation_note = QLabel(objectName="muted")
        self.flip_recommendation_note.setWordWrap(True)
        recommendation_layout.addWidget(self.flip_recommendation_headline)
        recommendation_layout.addWidget(self.flip_recommendation_rows)
        recommendation_layout.addWidget(self.flip_recommendation_note)
        layout.addWidget(recommendation)
        recommendation_actions = QHBoxLayout()
        self.track_top_button = QPushButton("Track recommended offers")
        self.track_top_button.setEnabled(False)
        self.track_top_button.clicked.connect(self._track_portfolio)
        recommendation_actions.addWidget(self.track_top_button)
        self.alternative_recommendation_button = QPushButton(
            "Recommend something else", objectName="secondary"
        )
        self.alternative_recommendation_button.setEnabled(False)
        self.alternative_recommendation_button.setToolTip(
            "Swap the current pick for the next-best combination that doesn't reuse any of "
            "these items."
        )
        self.alternative_recommendation_button.clicked.connect(self._recommend_alternative)
        recommendation_actions.addWidget(self.alternative_recommendation_button)
        recommendation_actions.addStretch()
        layout.addLayout(recommendation_actions)
        self.flip_table = self._table(
            ["Item", "Buy", "Sell", "Safe max", "Profit ea.", "ROI", "1h volume", "Limit", "Max potential", "Confidence"],
            minimum_widths={0: 220},
        )
        self._open_rows_with(
            self.flip_table, lambda row, _column: self._open_market_item(self.flip_table, row)
        )
        self._install_row_menu(self.flip_table, self._build_market_row_menu(self.flip_table))
        layout.addWidget(self.flip_table, 1)
        page.setLayout(layout)
        return page

    def _build_watchlist_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Watchlist",
            "Keep an eye on saved items and open any row for a full market breakdown.",
        )
        add_row = QHBoxLayout()
        self.watchlist_add_field = QLineEdit(placeholderText="Add an item by name…")
        self.watchlist_add_completer = QCompleter([])
        self.watchlist_add_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.watchlist_add_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.watchlist_add_field.setCompleter(self.watchlist_add_completer)
        self.watchlist_add_field.returnPressed.connect(self._add_watchlist_item)
        add_row.addWidget(self.watchlist_add_field, 1)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_watchlist_item)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)
        self.watchlist_add_status = QLabel(objectName="muted")
        self.watchlist_add_status.setWordWrap(True)
        self.watchlist_add_status.hide()
        layout.addWidget(self.watchlist_add_status)
        self.watchlist_empty = QLabel(
            "Your watchlist is empty. Add an item above, or double-click one in GE Flipper or "
            "Alch Finder.",
            objectName="status",
        )
        layout.addWidget(self.watchlist_empty)
        self.watchlist_table = self._table(
            ["Item", "Buy", "Sell", "Net margin", "ROI", "1h volume", "Oldest trade"],
            minimum_widths={0: 220},
        )
        self._open_rows_with(
            self.watchlist_table, lambda row, _column: self._open_market_item(self.watchlist_table, row)
        )
        self._install_row_menu(self.watchlist_table, self._build_market_row_menu(self.watchlist_table))
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
        self.journal_invested.setToolTip(
            "What the quantity actually sold cost to buy — the capital behind the realized "
            "profit beside it, so the two cover the same goods. A position still part-sold "
            "counts only the part that has sold; the rest is money still in the trade, not "
            "money it has traded with. Matches the Performance page card of the same name."
        )
        self.journal_attention = ClickableCard(
            "Needs attention\n0", objectName="summaryCard"
        )
        self.journal_attention.clicked.connect(self._reveal_attention_positions)
        self.journal_attention.setToolTip(
            "Listed for sale / Partially sold positions whose asking price is at least 2%\n"
            "above what the current market now suggests — an ask this stale is unlikely\n"
            "to fill.\n\n"
            "The asking price is the one you really listed at, taken from the Grand\n"
            "Exchange offer, so relisting nearer the suggestion clears the flag on the\n"
            "next sync. A position never listed through a synced offer is graded against\n"
            "its own sell suggestion instead.\n\n"
            "Click the card to jump to the rows it is counting."
        )
        summary.addWidget(self.journal_profit)
        summary.addWidget(self.journal_win_rate)
        summary.addWidget(self.journal_invested)
        summary.addWidget(self.journal_attention)
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

        # Your eight Grand Exchange slots, laid out the way the game lays them out. This
        # used to be a tab of its own holding a table, where six of its eight rows read
        # "Empty" and the two that mattered reported progress as a percentage you had to
        # stop and read. On the page it needs no clicking to reach, and _import_runelite_
        # events already re-renders it every three seconds, so it fills as the offers do.
        self.ge_offers_empty = QLabel(
            "Connect RuneLite with a character logged in to see your 8 Grand Exchange "
            "slots here, live.",
            objectName="status",
        )
        self.ge_offers_empty.setWordWrap(True)
        layout.addWidget(self.ge_offers_empty)
        self.ge_slots_frame = QFrame()
        self.ge_slots_frame.setToolTip(
            "Read straight from the RuneLite plugin's own Grand Exchange bookkeeping, not "
            "reconstructed from past sync events — so an offer just placed and not yet "
            "filled at all still shows up correctly."
        )
        slot_grid = QGridLayout(self.ge_slots_frame)
        slot_grid.setContentsMargins(0, 0, 0, 0)
        slot_grid.setSpacing(8)
        self.ge_slot_cards: list[GEOfferSlotCard] = []
        for slot_index in range(GE_SLOT_COUNT):
            card = GEOfferSlotCard(slot_index)
            card.clicked.connect(self._reveal_offer_in_journal)
            self.ge_slot_cards.append(card)
            slot_grid.addWidget(card, slot_index // 4, slot_index % 4)
        for column in range(4):
            slot_grid.setColumnStretch(column, 1)
        layout.addWidget(self.ge_slots_frame)
        # Only ever says why a click on a slot went nowhere, so it stays out of the way
        # until it has something to say.
        self.ge_slot_hint = QLabel(objectName="muted")
        self.ge_slot_hint.setWordWrap(True)
        self.ge_slot_hint.hide()
        layout.addWidget(self.ge_slot_hint)

        self.journal_tabs = QTabWidget()
        self.journal_tabs.currentChanged.connect(
            lambda _index: self._release_pending_flashes()
        )
        plans_tab = QWidget()
        plans_layout = QVBoxLayout(plans_tab)
        actions = QHBoxLayout()
        add_button = QPushButton("Add completed trade")
        add_button.clicked.connect(self._add_trade)
        update_button = QPushButton("Update selected trade", objectName="secondary")
        update_button.clicked.connect(self._update_selected_trade)
        delete_button = QPushButton("Delete selected", objectName="secondary")
        delete_button.clicked.connect(self._delete_selected_trade)
        self._journal_row_buttons += [update_button, delete_button]
        export_button = QPushButton("Export CSV", objectName="secondary")
        export_button.setToolTip(
            "Save every tracked position and manually entered trade to a CSV file, "
            "regardless of the status and period filters selected below."
        )
        export_button.clicked.connect(self._export_journal_csv)
        actions.addWidget(add_button)
        actions.addWidget(update_button)
        actions.addWidget(delete_button)
        actions.addWidget(export_button)
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
            "Show every journal entry or focus on one stage of the trade lifecycle. Supplies "
            "are quest or skilling buys marked out of your flip totals — select 'Update "
            "selected trade' and set the status to Supplies to move one there."
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
            text_columns={1, 2},
        )
        self._open_rows_with(
            self.journal_table, lambda _row, _column: self._update_selected_trade()
        )
        self._install_row_menu(self.journal_table, self._build_journal_row_menu)
        self._delete_selected_row_on_delete_key(self.journal_table, self._delete_selected_trade)
        self.journal_table.itemSelectionChanged.connect(self._journal_selection_changed)
        self._journal_selection_changed()
        plans_layout.addWidget(self.journal_table, 1)
        self.journal_tabs.addTab(plans_tab, _PLANS_TAB_TITLE)

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
        self._activity_row_buttons += [details_button, remove_button]
        sync_controls.addWidget(self.runelite_status, 1)
        sync_controls.addWidget(self.runelite_filter)
        sync_controls.addWidget(import_button)
        sync_controls.addWidget(details_button)
        sync_controls.addWidget(remove_button)
        activity_layout.addLayout(sync_controls)
        self.synced_trade_table = self._table(
            ["Time", "Source", "Character", "Trade", "Given", "Received", "Est. difference"],
            minimum_widths={0: 145, 2: 140, 3: 220, 4: 180, 5: 180, 6: 130},
            text_columns={1, 2, 3, 4, 5},
        )
        self._open_rows_with(
            self.synced_trade_table, lambda _row, _column: self._open_selected_synced_trade()
        )
        self._install_row_menu(self.synced_trade_table, self._build_activity_row_menu)
        self._delete_selected_row_on_delete_key(
            self.synced_trade_table, self._delete_selected_synced_trade
        )
        self.synced_trade_table.itemSelectionChanged.connect(self._activity_selection_changed)
        self._activity_selection_changed()
        activity_layout.addWidget(self.synced_trade_table, 1)
        self.journal_tabs.addTab(activity_tab, "RuneLite activity")

        buy_limits_tab = QWidget()
        buy_limits_layout = QVBoxLayout(buy_limits_tab)
        # Set in _render_buy_limits, which knows whether an empty table means "nothing is
        # limited" or "nothing is being counted".
        self.buy_limits_empty = QLabel(objectName="status")
        self.buy_limits_empty.setWordWrap(True)
        buy_limits_layout.addWidget(self.buy_limits_empty)
        self.buy_limits_table = self._table(
            ["Item", "Bought (4h)", "Limit", "Remaining", "Resets in"],
            minimum_widths={0: 230},
        )
        self.buy_limits_table.setToolTip(
            "Grand Exchange buy limits are a rolling 4-hour window — each purchase counts "
            "against the limit until exactly 4 hours after it happened, then drops back out "
            "on its own rather than all resetting at once."
        )
        buy_limits_layout.addWidget(self.buy_limits_table, 1)
        self.journal_tabs.addTab(buy_limits_tab, "Buy limits")

        supplies_tab = QWidget()
        supplies_layout = QVBoxLayout(supplies_tab)
        self.supplies_spend_total = QLabel("Total spent\n0 gp", objectName="summaryCard")
        self.supplies_spend_total.setToolTip(
            "Every position with status Supplies, all time — not scoped by the Period "
            "filter above, since a supplies buy rarely finishes on the day it starts."
        )
        supplies_summary = QHBoxLayout()
        supplies_summary.addWidget(self.supplies_spend_total)
        supplies_summary.addStretch()
        supplies_layout.addLayout(supplies_summary)
        self.supplies_spend_empty = QLabel(
            "No positions are marked Supplies yet. Select a row on the Plans && completed "
            "tab, choose Update selected trade, and set its status to Supplies.",
            objectName="status",
        )
        supplies_layout.addWidget(self.supplies_spend_empty)
        self.supplies_spend_table = self._table(
            ["Item", "Quantity", "Purchases", "Total spent", "Last bought"],
            minimum_widths={0: 230},
        )
        supplies_layout.addWidget(self.supplies_spend_table, 1)
        self.journal_tabs.addTab(supplies_tab, "Supplies spend")

        layout.addWidget(self.journal_tabs, 1)
        page.setLayout(layout)
        self._render_journal()
        self._render_synced_trades()
        self._render_buy_limits()
        self._render_ge_offers()
        return page

    def _build_performance_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "Performance",
            "Grades the plans in your Trade Journal against what actually happened. Every "
            "figure is realized from recorded fills — a projection never counts as a result.",
            market_refresh=False,
        )

        self.savings_goal_frame = QFrame(objectName="recommendation")
        goal_layout = QHBoxLayout(self.savings_goal_frame)
        self.savings_goal_label = QLabel("No savings goal set yet.")
        self.savings_goal_label.setWordWrap(True)
        goal_layout.addWidget(self.savings_goal_label, 1)
        self.savings_goal_progress = QProgressBar()
        self.savings_goal_progress.setRange(0, 100)
        self.savings_goal_progress.setFixedWidth(180)
        self.savings_goal_progress.setVisible(False)
        goal_layout.addWidget(self.savings_goal_progress)
        self.savings_goal_set_button = QPushButton("Set goal", objectName="secondary")
        self.savings_goal_set_button.setToolTip(
            "Give this a label and a target amount. Progress is tracked from realized "
            "profit — the same figures the cards below use — starting the moment you set it."
        )
        self.savings_goal_set_button.clicked.connect(self._edit_savings_goal)
        goal_layout.addWidget(self.savings_goal_set_button)
        self.savings_goal_clear_button = QPushButton("Clear goal", objectName="secondary")
        self.savings_goal_clear_button.setVisible(False)
        self.savings_goal_clear_button.clicked.connect(self._clear_savings_goal)
        goal_layout.addWidget(self.savings_goal_clear_button)
        layout.addWidget(self.savings_goal_frame)

        summary = QHBoxLayout()
        self.performance_profit = QLabel("Realized profit\n0 gp", objectName="summaryCard")
        self.performance_profit.setToolTip(
            "Profit after Grand Exchange tax from recorded sale fills, including the sold "
            "part of positions still in progress."
        )
        self.performance_return = QLabel("Return on capital\n—", objectName="summaryCard")
        self.performance_return.setToolTip(
            "Realized profit against the cost of the quantity actually sold — weighted by "
            "capital, so one large flip is not averaged away by a small one."
        )
        self.performance_win_rate = QLabel("Win rate\n—", objectName="summaryCard")
        self.performance_positions = QLabel("Results\n0", objectName="summaryCard")
        self.performance_positions.setToolTip(
            "Positions and manual entries that have produced realized proceeds."
        )
        self.performance_hold = QLabel("Median hold\n—", objectName="summaryCard")
        self.performance_hold.setToolTip(
            "Midpoint time from opening a position to finishing it. Manual entries and "
            "history saved before completion times were recorded are excluded."
        )
        for card in (
            self.performance_profit,
            self.performance_return,
            self.performance_win_rate,
            self.performance_positions,
            self.performance_hold,
        ):
            summary.addWidget(card)
        summary.addStretch()
        summary.addWidget(QLabel("Period", objectName="muted"))
        self.performance_period_filter = QComboBox()
        self.performance_period_filter.addItems(PERIOD_FILTERS)
        saved_period = str(QSettings().value("performance/period_filter", PERIOD_FILTERS[0]))
        self.performance_period_filter.setCurrentText(
            saved_period if saved_period in PERIOD_FILTERS else PERIOD_FILTERS[0]
        )
        self.performance_period_filter.setMinimumWidth(140)
        self.performance_period_filter.setToolTip(
            "Scope this page to finished history in this window. Positions still in "
            "progress always count, exactly as they do in the Trade Journal."
        )
        self.performance_period_filter.currentTextChanged.connect(
            self._performance_period_filter_changed
        )
        summary.addWidget(self.performance_period_filter)
        layout.addLayout(summary)

        self.performance_tabs = QTabWidget()

        strategy_tab = QWidget()
        strategy_layout = QVBoxLayout(strategy_tab)
        self.performance_strategy_empty = QLabel(
            "No completed trades in this period yet. Record a sale fill on a tracked "
            "position and its strategy appears here.",
            objectName="status",
        )
        self.performance_strategy_empty.setWordWrap(True)
        strategy_layout.addWidget(self.performance_strategy_empty)
        self.performance_strategy_table = self._table(
            [
                "Strategy",
                "Results",
                "Win rate",
                "Realized profit",
                "Return on capital",
                "Capital traded",
                "Median hold",
            ],
            minimum_widths={0: 170, 3: 130, 4: 130, 5: 125, 6: 100},
        )
        strategy_layout.addWidget(self.performance_strategy_table, 1)
        self.performance_tabs.addTab(strategy_tab, "By strategy")

        plan_tab = QWidget()
        plan_layout = QVBoxLayout(plan_tab)
        self.performance_plan_empty = QLabel(
            "Nothing to compare yet. A tracked position needs at least one recorded fill "
            "before its plan can be graded.",
            objectName="status",
        )
        self.performance_plan_empty.setWordWrap(True)
        plan_layout.addWidget(self.performance_plan_empty)
        self.performance_plan_table = self._table(
            ["Measure", "Planned", "Actual", "Drift", "Positions", "Note"],
            minimum_widths={0: 140, 1: 125, 2: 125, 3: 95, 5: 250},
            maximum_widths={5: 380},
            text_columns={5},
        )
        plan_layout.addWidget(self.performance_plan_table, 1)
        self.performance_tabs.addTab(plan_tab, "Plan vs. actual")

        item_tab = QWidget()
        item_layout = QVBoxLayout(item_tab)
        item_controls = QHBoxLayout()
        self.performance_item_all = QCheckBox("Include items traded only once")
        self.performance_item_all.setToolTip(
            "A single flip says little about an item. Off by default so the table ranks "
            "items you have actually traded repeatedly."
        )
        self.performance_item_all.toggled.connect(self._render_performance)
        item_controls.addWidget(self.performance_item_all)
        item_controls.addStretch()
        item_layout.addLayout(item_controls)
        self.performance_item_empty = QLabel(
            "No items with realized results in this period.", objectName="status"
        )
        self.performance_item_empty.setWordWrap(True)
        item_layout.addWidget(self.performance_item_empty)
        self.performance_item_table = self._table(
            [
                "Item",
                "Results",
                "Win rate",
                "Realized profit",
                "Return on capital",
                "Capital traded",
            ],
            minimum_widths={0: 230, 3: 140, 4: 145, 5: 135},
        )
        item_layout.addWidget(self.performance_item_table, 1)
        self.performance_tabs.addTab(item_tab, "By item")

        layout.addWidget(self.performance_tabs, 1)
        page.setLayout(layout)
        self._performance_ready = True
        self._render_performance()
        return page

    def _performance_period_filter_changed(self, period: str) -> None:
        QSettings().setValue("performance/period_filter", period)
        self._render_performance()

    def _render_performance(self) -> None:
        # _render_journal fires while the Journal page is still being built, before this
        # page's widgets exist.
        if not self._performance_ready:
            return
        period = self.performance_period_filter.currentText()
        # Local, not UTC, for the same reason the Journal uses a local clock: "Today" has
        # to mean the user's day.
        now = datetime.now().astimezone()
        tracked = [trade for trade in self._journal.list_tracked() if trade.status != "Supplies"]
        results = realized_results(tracked, self._journal.list_all(), period, now)

        summary = summarize(results)
        self.performance_profit.setText(f"Realized profit\n{_signed_gp(summary.realized_profit)}")
        self._set_money_state(self.performance_profit, summary.realized_profit)
        self.performance_return.setText(
            f"Return on capital\n{_percent(summary.return_on_capital)}"
        )
        self._set_money_state(
            self.performance_return,
            0 if summary.return_on_capital is None else summary.return_on_capital,
        )
        self.performance_win_rate.setText(f"Win rate\n{_percent(summary.win_rate)}")
        self.performance_positions.setText(f"Results\n{summary.positions:,}")
        self.performance_hold.setText(f"Median hold\n{_hold_time(summary.median_hold_hours)}")

        strategies = by_strategy(results)
        self.performance_strategy_empty.setVisible(not strategies)
        self._fill_table(
            self.performance_strategy_table,
            [_group_row(group, hold=True) for group in strategies],
            green_columns={3, 4},
        )

        rows = calibration(tracked, period, now)
        self.performance_plan_empty.setVisible(not rows)
        self._fill_table(
            self.performance_plan_table,
            [
                [
                    row.label,
                    _signed_gp(row.planned) if row.signed else _gp(row.planned),
                    _signed_gp(row.actual) if row.signed else _gp(row.actual),
                    _percent(row.drift, signed=True),
                    f"{row.positions:,}",
                    row.note,
                ]
                for row in rows
            ],
            green_columns=set(),
            decorate=lambda index: self._decorate_calibration_row(index, rows),
        )

        items = by_item(
            results, minimum_positions=1 if self.performance_item_all.isChecked() else 2
        )
        self.performance_item_empty.setVisible(not items)
        self._fill_table(
            self.performance_item_table,
            [_group_row(group, hold=False) for group in items],
            green_columns={3, 4},
        )
        self._render_savings_goal(tracked)

    def _saved_savings_goal(self) -> tuple[str, int, str] | None:
        """The persisted goal, or None if none is set. Stored in QSettings rather than the
        journal database — it's a single piece of app configuration, not trade history."""
        label = str(QSettings().value("savings_goal/label", ""))
        if not label:
            return None
        target = _setting_int("savings_goal/target", 0, minimum=1, maximum=2_000_000_000)
        created_at = str(
            QSettings().value("savings_goal/created_at", datetime.now(UTC).isoformat())
        )
        return label, target, created_at

    def _render_savings_goal(self, tracked: list[TrackedTrade]) -> None:
        goal = self._saved_savings_goal()
        self.savings_goal_clear_button.setVisible(goal is not None)
        if goal is None:
            self.savings_goal_label.setText("No savings goal set yet.")
            self.savings_goal_progress.setVisible(False)
            self.savings_goal_set_button.setText("Set goal")
            return
        label, target, created_at = goal
        self.savings_goal_set_button.setText("Edit goal")
        try:
            since = datetime.fromisoformat(created_at)
        except ValueError:
            since = datetime.now(UTC)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        saved = realized_profit_since(tracked, self._journal.list_all(), since)
        progress = SavingsProgress(label, target, saved)
        self.savings_goal_progress.setVisible(True)
        self.savings_goal_progress.setValue(int(progress.percent))
        self.savings_goal_progress.setFormat(_format_goal_percent(progress.percent))
        if progress.is_reached:
            self.savings_goal_label.setText(
                f"🎯 {label}: reached — {_gp(saved)} saved toward {_gp(target)}."
            )
            return
        now = datetime.now(UTC)
        rate = daily_profit_rate(tracked, self._journal.list_all(), now)
        eta_days = estimate_days_remaining(progress.remaining, rate)
        eta_text = (
            f" • {_format_eta(eta_days)} left at your last 7 days' rate"
            if eta_days is not None
            else " • no ETA yet — recent profit rate isn't positive"
        )
        self.savings_goal_label.setText(f"🎯 {label}: {_gp(saved)} / {_gp(target)}{eta_text}")

    def _edit_savings_goal(self) -> None:
        existing = self._saved_savings_goal()
        label, target = (existing[0], existing[1]) if existing is not None else ("", 0)
        dialog = SavingsGoalDialog(label, target, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = QSettings()
        settings.setValue("savings_goal/label", dialog.goal_label.text().strip())
        settings.setValue("savings_goal/target", dialog.goal_target.value())
        if existing is None:
            settings.setValue("savings_goal/created_at", datetime.now(UTC).isoformat())
        self._render_performance()

    def _clear_savings_goal(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear savings goal",
            "Remove this savings goal and its progress?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings = QSettings()
        settings.remove("savings_goal/label")
        settings.remove("savings_goal/target")
        settings.remove("savings_goal/created_at")
        self._render_performance()

    def _decorate_calibration_row(self, index: int, rows: list[CalibrationRow]) -> None:
        """Color the drift cell by whether the drift was in the trader's favor.

        Sign alone cannot decide this: paying *under* the buy target is a good outcome
        with a negative drift, so the generic green/red column rule would paint it red.
        """
        row = rows[index]
        table = self.performance_plan_table
        drift_cell = table.item(index, 3)
        if drift_cell is not None:
            if row.tone == "positive":
                drift_cell.setForeground(QColor(self._profit_color))
            elif row.tone == "negative":
                drift_cell.setForeground(QColor(self._loss_color))
        for column in range(table.columnCount()):
            cell = table.item(index, column)
            if cell is None:
                continue
            # The caveats are what stop these figures from being read as more precise than
            # they are, so every cell in the row carries them rather than one column.
            cell.setToolTip(f"{row.note}. {row.detail}")

    def _confidence_reading(self, confidence: int, floor: int) -> tuple[str, str]:
        """The colour a confidence score is drawn in, and what that colour is saying.

        One place, because the flip table and the recommendation card show the same score
        and must not grade it differently.
        """
        standing = confidence_standing(confidence, floor)
        if standing >= _CONFIDENCE_STRONG:
            return self._profit_color, "Strong for this strategy."
        if standing >= _CONFIDENCE_FAIR:
            return self._text_color, "Fair for this strategy."
        return (
            self._warning_color,
            "Only just clears this strategy's floor — treat it as thin.",
        )

    def _set_money_state(self, card: QLabel, value: float) -> None:
        card.setProperty(
            "moneyState", "positive" if value > 0 else "negative" if value < 0 else "neutral"
        )
        card.style().unpolish(card)
        card.style().polish(card)

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
        self._open_rows_with(
            self.alch_table, lambda row, _column: self._open_market_item(self.alch_table, row)
        )
        self._install_row_menu(self.alch_table, self._build_market_row_menu(self.alch_table))
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
                "Guide",
            ],
            minimum_widths={0: 250, 10: 320, 11: 110},
            maximum_widths={10: 360},
            text_columns={1, 9, 10, 11},
        )
        self.skill_table.cellDoubleClicked.connect(self._open_skill_guide)
        # Enter opens the guide from anywhere on the row, where a double-click has to land
        # on the Guide cell itself. A double-click lands wherever the pointer already was,
        # so it has to be told apart from one meant for the row; Enter on a row the player
        # has deliberately walked to is not ambiguous, and asking them to arrow across
        # eleven columns first would be the only way to reach it without a mouse.
        self.skill_table.rowActivated.connect(
            lambda row, _column: self._open_skill_guide(row, _SKILL_GUIDE_COLUMN)
        )
        self._install_row_menu(self.skill_table, self._build_guide_row_menu(
            self.skill_table, _SKILL_GUIDE_COLUMN, self._open_skill_guide
        ))
        layout.addWidget(self.skill_table, 1)
        page.setLayout(layout)
        return page

    def _build_pvm_page(self) -> QWidget:
        page = QWidget()
        layout = self._page_heading(
            "PvM Readiness",
            "Compares your synced gear, bank, and stats against a hand-picked checklist for "
            "popular bosses. Requirements and GP/hr are community estimates, not guarantees — "
            "double-click a row to verify against the OSRS Wiki.",
        )
        self.pvm_status = QLabel(objectName="status")
        self.pvm_status.setWordWrap(True)
        layout.addWidget(self.pvm_status)
        self.pvm_table = self._table(
            ["Activity", "Status", "Missing skills", "Missing gear", "Est. GP/hr", "Notes", "Guide"],
            minimum_widths={0: 200, 2: 220, 3: 260, 5: 260, 6: 110},
            maximum_widths={5: 380},
            text_columns={1, 2, 3, 5, 6},
        )
        self.pvm_table.cellDoubleClicked.connect(self._open_pvm_guide)
        self.pvm_table.rowActivated.connect(
            lambda row, _column: self._open_pvm_guide(row, _PVM_GUIDE_COLUMN)
        )
        self._install_row_menu(self.pvm_table, self._build_guide_row_menu(
            self.pvm_table, _PVM_GUIDE_COLUMN, self._open_pvm_guide
        ))
        layout.addWidget(self.pvm_table, 1)
        page.setLayout(layout)
        self._render_pvm()
        return page

    def _table(
        self,
        headers: list[str],
        *,
        minimum_widths: dict[int, int] | None = None,
        maximum_widths: dict[int, int] | None = None,
        text_columns: set[int] | None = None,
    ) -> QTableWidget:
        """A table sized from its content, within per-column bounds.

        ``maximum_widths`` overrides ``DEFAULT_MAXIMUM_COLUMN_WIDTH`` for columns that earn
        more room. A column's declared minimum always wins over its maximum.

        Columns are right-aligned by default, which is what the figures they mostly hold
        want. ``text_columns`` names the ones holding prose instead — a right-aligned
        sentence is read as a ragged left edge, and once elided it loses its beginning
        rather than its end.
        """
        table = ResponsiveTableWidget(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(64)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Qt defaults a freshly sorting-enabled table to an active column-0 descending
        # indicator, which silently re-sorts every future _fill_table() population by that
        # column instead of preserving the caller's intended (often score-ranked) order.
        # Clearing it here means tables show their real order until a user actually clicks
        # a header; tables that want an explicit default still call sortItems() themselves.
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        table.setProperty("minimumColumnWidths", minimum_widths or {})
        table.setProperty("maximumColumnWidths", maximum_widths or {})
        table.setProperty("textColumns", text_columns or set())
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
            self._release_pending_flashes()

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
        self._refresh_watchlist_completer()
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
        self._render_pvm()

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
        self._excluded_item_ids = {candidate.item_id for candidate in self._portfolio}

    def _recommend_alternative(self) -> None:
        """Replace the shown recommendation with the next-best combination that doesn't
        reuse any item already recommended this session (original pick or earlier
        alternatives), so repeated clicks keep moving through the ranked pool."""
        if not self._points:
            return
        remaining = [
            candidate for candidate in self._flips if candidate.item_id not in self._excluded_item_ids
        ]
        alternative = plan_flip_portfolio(
            remaining,
            cash_stack=self.cash.value(),
            slot_count=self.slots.value(),
        )
        if not alternative:
            QMessageBox.information(
                self,
                "No more alternatives",
                "No other qualifying combination is available right now. Try adjusting cash, "
                "GE slots, or strategy for more options.",
            )
            return
        self._portfolio = alternative
        self._excluded_item_ids |= {candidate.item_id for candidate in alternative}
        self._render_flips()

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
        floor = int(STRATEGIES[self.strategy.currentText()]["min_confidence"])

        def decorate_flip(row_index: int) -> None:
            # The one figure on this page saying how much to trust the rest, and it was the
            # only one drawn in plain text among columns of confident green.
            colour, reading = self._confidence_reading(rows[row_index].confidence, floor)
            cell = self.flip_table.item(row_index, 9)
            cell.setForeground(QColor(colour))
            cell.setToolTip(
                f"How much recent trading backs this margin: volume, how fresh the\n"
                f"prices are, and how steady they have been.\n"
                f"{self.strategy.currentText()} needs at least {floor}%.\n{reading}"
            )

        self._fill_table(
            self.flip_table,
            values,
            green_columns={4, 5},
            row_ids=[item.item_id for item in rows],
            decorate=decorate_flip,
        )
        if self._portfolio:
            self.track_top_button.setEnabled(True)
            self.track_top_button.setText(
                f"Track all {len(self._portfolio)} recommended offers"
            )
            self.alternative_recommendation_button.setEnabled(True)
            allocated = sum(item.capital_required for item in self._portfolio)
            expected_profit = sum(item.potential_profit for item in self._portfolio)
            remaining = self.cash.value() - allocated
            self.flip_recommendation_headline.setText(
                "<b>Our recommendations</b>"
                f'<span style="color:{self._muted_color}"> — {self.strategy.currentText()}'
                f" &nbsp;•&nbsp; {len(self._portfolio)}/{self.slots.value()} GE slots"
                f" &nbsp;•&nbsp; {_gp(allocated)} allocated"
                f" &nbsp;•&nbsp; {_gp(remaining)} held back</span>"
            )
            self.flip_recommendation.setText(self._portfolio_html())
            self.flip_recommendation_note.setText(
                f"Combined estimate if every buy and sell fills: {_gp(expected_profit)}. "
                "Held-back cash has no qualifying size under the current liquidity, limit, and "
                "risk caps. Targets are patient limit offers; fills are not guaranteed."
            )
            self.flip_recommendation_rows.show()
            self.flip_recommendation_note.show()
        else:
            self.track_top_button.setEnabled(False)
            self.track_top_button.setText("Track recommended offers")
            self.alternative_recommendation_button.setEnabled(False)
            self.flip_recommendation_headline.setText(
                "No opportunity currently passes this mode’s affordability, liquidity, "
                "stability, and confidence rules."
            )
            self.flip_recommendation.clear()
            self.flip_recommendation_rows.hide()
            self.flip_recommendation_note.hide()
        self.flip_recommendation_rows.fit()

    def _portfolio_html(self) -> str:
        """The plan as one row per offer, so the numbers line up in columns instead of
        running together in a paragraph each."""
        muted = self._muted_color
        floor = int(STRATEGIES[self.strategy.currentText()]["min_confidence"])
        # Every column but the item name is fixed, so the item name takes all the slack and
        # the figures stay in one block on the right instead of drifting apart across a
        # wide window. Fixed rather than proportional widths: a percentage cannot shrink,
        # so it makes the label ask for a height far taller than the text it holds, and the
        # card would scroll a plan it is in fact showing in full.
        headers = "".join(
            f'<td align="{alignment}"{width} style="color:{muted}">{title}</td>'
            for title, alignment, width in (
                ("", "left", ' width="26"'),
                ("Item", "left", ""),
                ("Buy ea.", "right", ' width="120"'),
                ("Sell ea.", "right", ' width="120"'),
                ("Total cost", "right", ' width="120"'),
                ("Est. profit", "right", ' width="140"'),
                ("Confidence", "right", ' width="110"'),
            )
        )
        rows = [f"<tr>{headers}</tr>"]
        for number, item in enumerate(self._portfolio, start=1):
            rows.append(
                "<tr>"
                f'<td style="color:{muted}">{number}</td>'
                f"<td><b>{html.escape(item.name)}</b>"
                f'<span style="color:{muted}"> × {item.suggested_quantity:,}</span></td>'
                f'<td align="right">{item.buy_price:,}</td>'
                f'<td align="right">{item.sell_price:,}</td>'
                f'<td align="right" style="color:{muted}">{item.capital_required:,}</td>'
                f'<td align="right" style="color:{self._profit_color}">'
                f"<b>{_signed_gp(item.potential_profit)}</b></td>"
                f'<td align="right" style="color:'
                f'{self._confidence_reading(item.confidence, floor)[0]}">'
                f"{item.confidence}%</td>"
                "</tr>"
            )
        return f'<table width="100%" cellspacing="0" cellpadding="5">{"".join(rows)}</table>'

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
                or trade.status in {"Completed", "Cancelled", "Supplies"}
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

    def _refresh_watchlist_completer(self) -> None:
        names = sorted(item.name for item in self._mappings.values())
        self.watchlist_add_completer.setModel(QStringListModel(names, self.watchlist_add_completer))

    def _add_watchlist_item(self) -> None:
        typed = self.watchlist_add_field.text().strip()
        if not typed:
            return
        match = next(
            (item for item in self._mappings.values() if item.name.casefold() == typed.casefold()),
            None,
        )
        if match is None:
            self.watchlist_add_status.setText(f"No item found named “{typed}”.")
            self.watchlist_add_status.show()
            return
        self.watchlist_add_field.clear()
        if match.item_id in self._watchlist:
            self.watchlist_add_status.setText(f"{match.name} is already on your watchlist.")
            self.watchlist_add_status.show()
            return
        self._watchlist.add(match.item_id)
        QSettings().setValue("market/watchlist", sorted(self._watchlist))
        self.watchlist_add_status.hide()
        self._render_watchlist()

    def _journal_status_filter_changed(self, selected_filter: str) -> None:
        QSettings().setValue("journal/status_filter", selected_filter)
        self._render_journal()

    def _journal_period_filter_changed(self, selected_period: str) -> None:
        QSettings().setValue("journal/period_filter", selected_period)
        self._render_journal()

    def _render_journal(self) -> None:
        tracked = self._journal.list_tracked()
        trades = self._journal.list_all()
        # Every journal mutation funnels through here, so this is the one place that can
        # see a position change hands: a buy finishing, or a sale closing the flip out.
        statuses = {trade.position_id: trade.status for trade in tracked}
        self._flash_journal_rows(journal_alert_positions(self._journal_statuses, statuses))
        self._journal_statuses = statuses
        selected_filter = self.journal_status_filter.currentText()
        selected_period = self.journal_period_filter.currentText()
        # Local, not UTC: "Today" has to mean the user's calendar day, the same day
        # boundary _refresh_overnight_suggestions already works from.
        now = datetime.now().astimezone()
        point_by_id = {point.item_id: point for point in self._points}
        # Read once for the whole table rather than per row: this is a file read.
        slots = self._placed_offers()
        placed_item_ids = self._items_with_a_live_buy_offer(slots)
        if self._adopt_live_asks(tracked, self._live_sell_asks(slots)):
            tracked = self._journal.list_tracked()

        def _live_sell_price(trade: TrackedTrade) -> int | None:
            point = point_by_id.get(trade.item_id) if trade.item_id is not None else None
            if point is None:
                return None
            try:
                _live_buy_price, live_sell_price = offer_targets(point)
            except ValueError:
                return None
            return live_sell_price

        # Computed over every tracked position, not just the ones the current status and
        # period filters happen to show — this is a "something needs a look" signal, not a
        # scoped statistic, so switching filters must never make it silently read zero.
        # The explanation behind each flag is built here too: the table has no column for
        # either the live suggestion or the real ask, so without this the tooltip could only
        # describe the flag, never show the arithmetic — leaving no way to tell it apart from
        # a stale flag, or a bug.
        attention_detail: dict[int, str] = {}
        live_sell_by_id: dict[int, int] = {}
        for trade in tracked:
            live_sell_price = _live_sell_price(trade)
            if live_sell_price is not None:
                live_sell_by_id[trade.position_id] = live_sell_price
            if live_sell_price is not None and trade_needs_attention(
                trade.status, trade.asking_price, live_sell_price
            ):
                attention_detail[trade.position_id] = _attention_tooltip(
                    trade.asking_price, live_sell_price
                )
        attention_positions = set(attention_detail)

        rendered_rows: list[_JournalRow] = []
        # Position id -> (what to actually ask, whether that clears what was paid). Built
        # alongside the row text above, from the same trade, so the highlight below and
        # the cell it highlights can never show two different numbers for one row.
        ask_detail: dict[int, tuple[int, bool]] = {}
        for trade in tracked:
            if not journal_status_matches(trade.status, selected_filter):
                continue
            if not tracked_position_within_period(trade.completed_at, selected_period, now):
                continue
            profit = journal_pl_presentation(
                trade.status,
                trade.estimated_profit,
                trade.realized_profit,
                trade.unsold_stock,
            )
            needs_attention = trade.position_id in attention_positions
            display_status = journal_display_status(
                trade.status, trade.bought_quantity, trade.item_id, placed_item_ids
            )
            # A row sitting at "Bought" shows a different number in this same cell — see
            # ``_ask_price`` — so the cell text has to be built from that, not the plan's
            # own frozen target, or the highlight below would point at the right cell with
            # the wrong number in it.
            sell_cell_price = trade.sell_suggestion
            if trade.status == READY_TO_SELL_STATUS:
                sell_cell_price, ask_is_sound = _ask_price(
                    trade, live_sell_by_id.get(trade.position_id)
                )
                ask_detail[trade.position_id] = (sell_cell_price, ask_is_sound)
            rendered_rows.append(
                _JournalRow(
                    [
                        trade.created_at[:10],
                        f"● {display_status}",
                        f"⚠ {trade.item_name}" if needs_attention else trade.item_name,
                        f"{trade.quantity:,}",
                        f"{_gp(trade.buy_suggestion)} ↻"
                        if trade.suggestion_was_refreshed
                        else _gp(trade.buy_suggestion),
                        f"{_gp(trade.average_buy_price)} avg"
                        if trade.average_buy_price is not None
                        else "—",
                        _gp(sell_cell_price)
                        if trade.status == READY_TO_SELL_STATUS
                        else (
                            f"{_gp(trade.sell_suggestion)} ↻"
                            if trade.suggestion_was_refreshed
                            else _gp(trade.sell_suggestion)
                        ),
                        f"{_gp(trade.average_sell_price)} avg"
                        if trade.average_sell_price is not None
                        else "—",
                        profit.text,
                    ],
                    trade.position_id,
                    display_status,
                    trade.status,
                    profit,
                    needs_attention,
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
                _JournalRow(
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
                    raw_status,
                    profit,
                    # A manual entry has no live market comparison to grade its price
                    # against — it records a completed outcome, not an open ask.
                    False,
                )
            )

        def decorate_row(row_index: int) -> None:
            row = rendered_rows[row_index]
            # Only a tracked position carries a flash key. Manual entries are numbered from
            # their own table, so a trade_id and a position_id can be the same integer and
            # would light each other's row; and nothing ever happens to a manual entry on
            # its own anyway, since it records an outcome rather than an open trade.
            if row.raw_status in UpdateTrackedTradeDialog.STATUSES:
                self.journal_table.item(row_index, 0).setData(
                    _FLASH_KEY_ROLE, row.record_id
                )
            status_cell = self.journal_table.item(row_index, 1)
            status_cell.setData(Qt.ItemDataRole.UserRole, row.raw_status)
            status_cell.setToolTip(
                "Planned but not placed — no Grand Exchange slot is holding a buy for this. "
                "Update or delete it like any other pending buy."
                if row.display_status == PLANNED_STATUS
                else row.raw_status
            )
            if isinstance(status_cell, SortableTableItem):
                status_cell.sort_value = (
                    _JOURNAL_STATUS_ORDER.get(row.display_status, len(_JOURNAL_STATUS_ORDER)),
                    row.display_status,
                )
            status_font = status_cell.font()
            status_font.setBold(True)
            status_cell.setFont(status_font)
            status_cell.setForeground(
                QColor(self._journal_status_colors.get(row.display_status, self._muted_color))
            )
            if row.raw_status == READY_TO_SELL_STATUS:
                # Bought, collected, and nothing listed yet: the only thing left to do with
                # this row is go and ask a price for it. The blink says which row the moment
                # the buy lands, but it is over in two seconds and the player is still in
                # game — so the figure they came back for stays picked out until it is
                # listed, in the same amber the status beside it is already written in.
                ask_cell = self.journal_table.item(row_index, 6)
                ask_font = ask_cell.font()
                ask_font.setBold(True)
                ask_cell.setFont(ask_font)
                _ask_price_value, ask_is_sound = ask_detail.get(
                    row.record_id, (None, True)
                )
                if ask_is_sound:
                    ask_cell.setForeground(QColor(self._warning_color))
                    ask_cell.setToolTip("Ready to list.\nThis is the price to ask for it.")
                else:
                    # The live market, and the plan this flip was tracked at, both fail to
                    # clear what was actually paid for it right now — dressing that number
                    # up as sound advice is how a highlight meant to help pointed someone
                    # at their own buy price instead of a real number to ask.
                    ask_cell.setForeground(QColor(self._loss_color))
                    ask_cell.setToolTip(
                        "Ready to list, but neither the market nor the original plan "
                        "clears what this cost — listing here would sell at a loss "
                        "after tax. Use your own judgement on the price."
                    )

            attention = attention_detail.get(row.record_id) if row.needs_attention else None
            if row.needs_attention:
                self.journal_table.item(row_index, 2).setForeground(
                    QColor(self._warning_color)
                )
            if attention is not None:
                # Every cell, not just the ⚠ one. The flag is about the row, so hunting for
                # the single cell that explains it — while the neighbours answer with their
                # own text — is the wrong way round. Only the P/L cell has something of its
                # own worth keeping here, and it keeps it below.
                for column in range(self.journal_table.columnCount()):
                    self.journal_table.item(row_index, column).setToolTip(attention)

            profit_cell = self.journal_table.item(row_index, 8)
            profit_cell.setData(Qt.ItemDataRole.UserRole, row.profit.tone)
            if row.profit.tone == "positive":
                profit_cell.setForeground(QColor(self._profit_color))
            elif row.profit.tone == "negative":
                profit_cell.setForeground(QColor(self._loss_color))
            elif row.profit.tone == "muted":
                profit_cell.setForeground(QColor(self._muted_color))
            profit_cell.setToolTip(
                f"{attention}\n\n{row.profit.tooltip}"
                if attention is not None
                else row.profit.tooltip
            )

        self.journal_filter_empty.setVisible(not rendered_rows)
        # Decorating through _fill_table, not after it: the Status cell's sort key is set
        # here, and a key applied after the table is already sorting by that column both
        # arrives too late to be sorted on and shuffles rows out from under this loop.
        self._fill_table(
            self.journal_table,
            [row.cells for row in rendered_rows],
            green_columns=set(),
            row_ids=[row.record_id for row in rendered_rows],
            decorate=decorate_row,
        )
        # A re-render mid-blink builds fresh cells with no highlight on them, so the beat
        # currently showing has to be painted back on.
        self._paint_journal_flash()

        # These cards and the Performance page's are the same three figures, so they come
        # from the same two helpers rather than from arithmetic repeated here. Computing
        # them twice is how "Capital traded" came to mean the whole outlay on this page and
        # the cost of what actually sold on that one — two numbers under one name. The only
        # thing that may now differ between the pages is which period each is scoped to.
        summary = summarize(
            realized_results(
                [trade for trade in tracked if trade.status != "Supplies"],
                trades,
                selected_period,
                now,
            )
        )
        self.journal_profit.setText(f"Realized profit\n{_signed_gp(summary.realized_profit)}")
        self._set_money_state(self.journal_profit, summary.realized_profit)
        self.journal_win_rate.setText(f"Win rate\n{_percent(summary.win_rate)}")
        self.journal_invested.setText(f"Capital traded\n{_gp(summary.capital_traded)}")
        self._attention_positions = attention_positions
        self.journal_attention.set_live(bool(attention_positions))
        self.journal_attention.setText(f"Needs attention\n{len(attention_positions):,}")
        # Negated: any positive count should read as the card's "negative" (warning) tone;
        # zero should read neutral. There is no profit/loss sign to reuse here directly.
        self._set_money_state(self.journal_attention, -len(attention_positions))
        self._render_supplies_spend(tracked)
        # Every journal mutation already funnels through here, so this is the one hook the
        # Performance page needs to stay in step with the data it analyzes.
        self._render_performance()
        # A filter change re-renders without ever calling _flash_journal_rows (nothing new
        # just finished), which is the one path that otherwise calls this — so a flash
        # queued behind the old filter and still waiting gets no second chance to play
        # without it running here too, on every render.
        self._release_pending_flashes()
        # Rebuilding the table above may have dropped the selection the row buttons were
        # enabled for; Qt only announces that when rows actually go, so ask directly.
        self._journal_selection_changed()

    def _render_supplies_spend(self, tracked: list[TrackedTrade]) -> None:
        rows = supplies_spend_rows(tracked)
        self.supplies_spend_total.setText(f"Total spent\n{_gp(total_supplies_spend(tracked))}")
        self.supplies_spend_empty.setVisible(not rows)
        values = [
            [
                row.item_name,
                f"{row.quantity:,}",
                f"{row.purchases:,}",
                _gp(row.spent),
                row.last_bought[:10],
            ]
            for row in rows
        ]
        self._fill_table(self.supplies_spend_table, values, green_columns=set())

    def _suggested_sell_prices(self) -> dict[int, int]:
        """The live passive sell target for every item with a current market snapshot.

        Lets an untracked buy fill or offer seed a real profit estimate instead of one that
        reads as guaranteed break-even. Empty before the first market load completes, which
        ``apply_offer_opened``/``apply_synced_ge_fill`` treat the same as no suggestion at all.
        """
        prices: dict[int, int] = {}
        for point in self._points:
            try:
                _buy_price, sell_price = offer_targets(point)
            except ValueError:
                # No usable observation on one side (e.g. an item with no trade history yet).
                continue
            prices[point.item_id] = sell_price
        return prices

    def _import_runelite_events(self) -> None:
        try:
            result = self._sync_importer.import_pending(
                self._journal, self._suggested_sell_prices()
            )
        except Exception as exc:  # noqa: BLE001 - a transient local failure must not break the UI loop.
            self._last_sync_message = f" • sync paused: {exc}"
            self._update_runelite_status()
            return
        if result.imported:
            label = "trade" if result.imported == 1 else "trades"
            self._last_sync_message = f" • imported {result.imported} new {label}"
            if result.applied_to_tracked:
                position_label = "position" if result.applied_to_tracked == 1 else "positions"
                self._last_sync_message += (
                    f" • {result.applied_to_tracked} tracked {position_label} updated"
                )
        elif result.rejected:
            self._last_sync_message = (
                f" • {result.rejected} invalid event"
                f"{'s' if result.rejected != 1 else ''} quarantined"
            )
        elif result.skipped:
            # Held, not lost: the RuneLite plugin has outrun this app, so these wait in the
            # queue for a version that understands them.
            self._last_sync_message = (
                f" • {result.skipped} event{'s' if result.skipped != 1 else ''} waiting"
                " for a newer version of OSRS Toolkit"
            )
        elif result.duplicates:
            self._last_sync_message = " • already up to date"
        self._update_runelite_status()
        if result.imported or result.duplicates:
            self._render_synced_trades()
        if result.imported:
            self._loadout_snapshot = self._journal.get_latest_loadout_snapshot()
            self._render_pvm()
            if result.applied_to_tracked:
                self._render_journal()
        # Recomputed on every tick, not just when something imports: purchases age out of
        # the rolling 4-hour buy-limit window purely by the clock moving forward, so the
        # "Resets in" countdown and remaining counts have to keep advancing on their own.
        self._render_buy_limits()
        # Also unconditional: the plugin updates its offer-state file on every Grand
        # Exchange change regardless of whether that change also produced a sync event this
        # pass imported, so this has to be read fresh every tick to stay live.
        self._render_ge_offers()

    def _render_buy_limits(self) -> None:
        limits = {
            item.item_id: item.buy_limit
            for item in self._mappings.values()
            if item.buy_limit is not None
        }
        now = datetime.now(UTC)
        # Bounded to the window this actually reads. _import_runelite_events calls this every
        # three seconds, so loading the whole imported history each time makes the app slower
        # the longer it is used; buy_limit_status ignores everything older than 4 hours anyway.
        statuses = buy_limit_status(
            self._journal.list_synced_trades("ge_fill", since=now - BUY_LIMIT_WINDOW),
            limits,
            now,
        )
        self.buy_limits_empty.setVisible(not statuses)
        # An empty table without the plugin is not the same claim as an empty table with it.
        # Read as "nothing is limited", it says you are clear to buy — which is exactly what
        # nobody can know here, since manual journal entries are not counted against a limit.
        if self._sync_importer.plugin_detected:
            self.buy_limits_empty.setText(
                "Nothing is currently limited. Items you've bought through RuneLite in the "
                "last 4 hours show up here once they're close to their Grand Exchange buy "
                "limit."
            )
        else:
            self.buy_limits_empty.setText(
                "Buy limits are counted from Grand Exchange purchases imported by the "
                "RuneLite plugin, which is not connected — so this tab stays empty whatever "
                "you have bought. It is not saying you have room left. Trades entered by "
                "hand in the journal are not counted against a limit either."
            )
        values = [
            [
                status.item_name,
                f"{status.bought_recently:,}",
                f"{status.limit:,}",
                f"{status.remaining:,}",
                _format_countdown(
                    max(0, int((datetime.fromisoformat(status.resets_at) - now).total_seconds()))
                ),
            ]
            for status in statuses
        ]
        self._fill_table(self.buy_limits_table, values, green_columns=set())

    def _placed_offers(self) -> dict[int, GEOfferSlot] | None:
        """The account's Grand Exchange slots, or None when there is nothing to read them
        from — no account known yet, or no readable slot state from the plugin.

        Read once per render and asked several questions, because it is a file read.
        """
        account_hash = self._sync_importer.connection_status().account_hash
        if account_hash is None:
            return None
        return self._sync_importer.read_placed_offers(account_hash)

    @staticmethod
    def _items_with_a_live_buy_offer(
        slots: dict[int, GEOfferSlot] | None,
    ) -> frozenset[int] | None:
        """Items one of the eight Grand Exchange slots is currently holding a buy for.

        Empty when the Grand Exchange is empty — every slot collected is the ordinary way to
        have no offers, and it is exactly when every pending row is a plan. None only when
        there is nothing to judge against, so the Journal can tell "not placed" apart from
        "cannot say". A slot counts while it holds the offer, including one finished and
        waiting to be collected: the offer is still there until the player takes it.
        """
        if slots is None:
            return None
        return frozenset(
            slot.item_id for slot in slots.values() if slot.side == "buy" and slot.item_id > 0
        )

    @staticmethod
    def _live_sell_asks(slots: dict[int, GEOfferSlot] | None) -> dict[int, int]:
        """What each item on the Grand Exchange is currently listed at."""
        if slots is None:
            return {}
        return {
            slot.item_id: slot.offer_price
            for slot in slots.values()
            if slot.side == "sell" and slot.item_id > 0 and slot.offer_price > 0
        }

    def _adopt_live_asks(self, tracked: list[TrackedTrade], asks: dict[int, int]) -> bool:
        """Record what the Grand Exchange says a listed position is really asking.

        ``apply_offer_opened`` writes this the moment an offer is placed, which covers every
        offer placed from now on. It cannot cover the ones already sitting on the Grand
        Exchange when this version arrived: those had their moment before there was anywhere
        to put the price, so they went on being graded against the sell target they were
        planned at, and the only way to correct one was to cancel it and list it again. The
        slots know the real number; this takes it.

        Writes only where it disagrees, so a render costs nothing once they agree. Returns
        whether anything changed, since the caller is holding positions that just went stale.
        """
        changed = False
        for trade in tracked:
            if trade.item_id is None or trade.status not in _LISTED_STATUSES:
                continue
            ask = asks.get(trade.item_id)
            if ask is None or ask == trade.listed_sell_price:
                continue
            self._journal.record_listed_price(trade.position_id, ask)
            changed = True
        return changed

    def _render_ge_offers(self) -> None:
        account_hash = self._sync_importer.connection_status().account_hash
        self.ge_offers_empty.setVisible(account_hash is None)
        self.ge_slots_frame.setVisible(account_hash is not None)
        if account_hash is None:
            # Forgotten rather than kept: whatever is on the slots when a character next
            # connects is where that character already was, not something that just
            # happened, so the next read has to seed afresh.
            self._ge_slot_states = None
            return
        slots = self._sync_importer.read_offer_state(account_hash)
        states = {slot_index: slot.state for slot_index, slot in slots.items()}
        self._flash_ge_slots(
            newly_reached(self._ge_slot_states, states, TERMINAL_OFFER_STATES)
        )
        self._ge_slot_states = states
        for slot_index, card in enumerate(self.ge_slot_cards):
            slot = slots.get(slot_index)
            if slot is None:
                card.show_empty()
            else:
                card.show_offer(slot)
        # show_empty/show_offer both reset the card's look, so the beat currently showing
        # has to be put back on top of it.
        self._paint_slot_flash()

    def _flash_journal_rows(self, position_ids: Iterable[int]) -> None:
        """Queue an attention blink on these journal rows."""
        pending = set(position_ids)
        if not pending:
            return
        self._pending_journal_flash |= pending
        self._release_pending_flashes()

    def _flash_ge_slots(self, slot_indexes: Iterable[int]) -> None:
        """Queue an attention blink on these Grand Exchange slot cards."""
        pending = set(slot_indexes)
        if not pending:
            return
        self._pending_slot_flash |= pending
        self._release_pending_flashes()

    def _release_pending_flashes(self) -> None:
        """Play whatever is queued and currently showing, once there is somebody there to
        see it.

        A buy finishes while the player is in RuneLite rather than in front of this
        window, which is precisely when a two-second blink is spent on nobody. So a flash
        waits until its surface is both on screen and being looked at — this window
        focused and not minimised, the Trade Journal page showing, and for the table its
        tab showing too — and meanwhile the sidebar carries a dot saying something is
        waiting there. The slot cards sit above the tabs, so they need only the page.

        A journal row waits on one more thing: the default Status filter is "Active
        trades", and a sale finishing is exactly the transition that drops a row out of
        it. Delivering the flash there anyway — the ordinary meaning of "queued" — would
        mark it seen against a row nobody was ever shown, and the dot that was the only
        trace of it would go dark with the flip's own conclusion never actually witnessed.
        Only the positions the current filter will actually render are delivered; the
        rest stay queued so the dot holds until a wider filter — "All statuses",
        "Completed" — brings the row into view, at which point this runs again on the
        re-render the filter change already triggers and catches them then.
        """
        if getattr(self, "journal_tabs", None) is None:
            return  # Still being built; nothing is on screen to flash yet.
        watched = self.isVisible() and not self.isMinimized() and self.isActiveWindow()
        on_page = watched and self.pages.currentIndex() == self._journal_page_index()
        if on_page and self._pending_slot_flash:
            self._slot_flasher.start(self._pending_slot_flash)
            self._pending_slot_flash.clear()
        if (
            on_page
            and self._pending_journal_flash
            and self.journal_tabs.currentIndex() == _PLANS_TAB_INDEX
        ):
            selected_filter = self.journal_status_filter.currentText()
            shown = {
                position_id
                for position_id in self._pending_journal_flash
                if journal_status_matches(
                    self._journal_statuses.get(position_id, ""), selected_filter
                )
            }
            if shown:
                self._journal_flasher.start(shown)
                self._pending_journal_flash -= shown
        self._update_journal_badge()

    def _journal_page_index(self) -> int:
        return self.NAV_ITEMS.index("Trade Journal")

    def _update_journal_badge(self) -> None:
        """Mark the sidebar and the tab while a blink is waiting to be seen.

        The blink itself is over in two seconds and only ever plays when someone is
        looking; this is what is left for the player who was in-game at the time, and it
        stays put until they arrive.
        """
        nav_item = self.nav.item(self._journal_page_index())
        if nav_item is None or getattr(self, "journal_tabs", None) is None:
            return
        waiting = bool(self._pending_journal_flash or self._pending_slot_flash)
        title = self.NAV_ITEMS[self._journal_page_index()]
        nav_item.setText(f"{title}  ●" if waiting else title)
        nav_item.setForeground(QColor(self._flash_color) if waiting else QBrush())
        nav_item.setToolTip(
            "An offer finished while you were elsewhere — open the journal to see which. "
            "If it still doesn't show, it may have finished the flip: widen Status to "
            "\"All statuses\" to find it."
            if waiting
            else f"{title}  (Ctrl+{self._journal_page_index() + 1})"
        )
        self.journal_tabs.setTabText(
            _PLANS_TAB_INDEX,
            _PLANS_TAB_TITLE + ("  ●" if self._pending_journal_flash else ""),
        )

    def _paint_journal_flash(self) -> None:
        """Wash the blinking rows, and take the wash back off on the dark beat."""
        table = self.journal_table
        lit_brush = QBrush(QColor(self._flash_row_color))
        for row in range(table.rowCount()):
            anchor = table.item(row, 0)
            if anchor is None:
                continue
            key = anchor.data(_FLASH_KEY_ROLE)
            # A null brush, not the table's own background colour: that is what hands the
            # row back to the alternating-row colours instead of freezing it on one.
            brush = lit_brush if self._journal_flasher.is_lit(key) else QBrush()
            for column in range(table.columnCount()):
                cell = table.item(row, column)
                # Only where it actually changes: this runs six times a blink, and setting
                # a cell to the brush it already has still repaints it. On a journal grown
                # to a few hundred rows that is the whole table redrawn per beat, to say
                # nothing about all but one row of it.
                if cell is not None and cell.background() != brush:
                    cell.setBackground(brush)

    def _paint_slot_flash(self) -> None:
        for slot_index, card in enumerate(self.ge_slot_cards):
            card.set_flashing(self._slot_flasher.is_lit(slot_index))

    def _reveal_offer_in_journal(self, item_id: int) -> None:
        """Answer "which row is this offer?" from the slot card's side.

        The blink points at the row when something happens; this is the same question
        asked at any other moment, by the player looking at a slot who wants the position
        behind it. A row the filters are hiding is not an answer, so they are widened
        until it shows — both are dropdowns a couple of inches below the slot that was
        clicked, so finding them changed is no mystery.
        """
        self.ge_slot_hint.hide()
        position_id = self._journal_position_for_item(item_id)
        if position_id is None:
            name = self._mappings[item_id].name if item_id in self._mappings else "This item"
            self.ge_slot_hint.setText(
                f"{name} has no journal row yet. Offers placed while RuneLite is connected "
                "start tracking themselves within a few seconds."
            )
            self.ge_slot_hint.show()
            return
        self.journal_tabs.setCurrentIndex(_PLANS_TAB_INDEX)
        if not self._select_journal_position(position_id):
            # Each of these re-renders the table on its way through, so the second look
            # sees the widened result. A completed flip can be out of the period window as
            # easily as out of the status one, so both give way.
            self.journal_status_filter.setCurrentText(JOURNAL_STATUS_FILTERS[0])
            self.journal_period_filter.setCurrentText(PERIOD_FILTERS[0])
            if not self._select_journal_position(position_id):
                return
        # focus, not start: see AttentionFlasher.focus. The player is asking about this slot
        # now, not about this slot as well as the last one they clicked.
        self._journal_flasher.focus({position_id})

    def _reveal_attention_positions(self) -> None:
        """Answer the Needs attention card: show me the rows you are counting.

        The same journey ``_reveal_offer_in_journal`` makes from a slot card, for the same
        reason — a row the filters are hiding is not an answer, so they give way — but for a
        set rather than one row. The newest is selected and scrolled to and the whole set
        blinks, because the count is a count: pointing at one of two would be answering half
        the question.
        """
        if not self._attention_positions:
            return
        self.journal_tabs.setCurrentIndex(_PLANS_TAB_INDEX)
        if not self._select_journal_position(next(iter(self._attention_positions))):
            self.journal_status_filter.setCurrentText(JOURNAL_STATUS_FILTERS[0])
            self.journal_period_filter.setCurrentText(PERIOD_FILTERS[0])
            if not self._select_journal_position(next(iter(self._attention_positions))):
                return
        self._journal_flasher.focus(self._attention_positions)

    def _journal_position_for_item(self, item_id: int) -> int | None:
        """The journal row a Grand Exchange slot is about.

        The newest position for the item that is still in progress; failing that the
        newest of any status, so a finished flip whose coins are still uncollected can be
        found too. ``list_tracked`` already returns newest first.
        """
        matches = [trade for trade in self._journal.list_tracked() if trade.item_id == item_id]
        chosen = next(
            (
                trade
                for trade in matches
                if journal_status_matches(trade.status, "Active trades")
            ),
            matches[0] if matches else None,
        )
        return chosen.position_id if chosen is not None else None

    def _select_journal_position(self, position_id: int) -> bool:
        """Select and scroll to a position's row, if the current filters are showing it."""
        table = self.journal_table
        for row in range(table.rowCount()):
            anchor = table.item(row, 0)
            if anchor is not None and anchor.data(_FLASH_KEY_ROLE) == position_id:
                table.selectRow(row)
                table.scrollToItem(anchor, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

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
        # A partially filling GE offer reports one event per fill tick, so without grouping
        # a single large buy/sell can flood this feed with dozens of near-identical rows.
        # Fills that share an offer_id (set once an offer starts and carried through every
        # continuation of it) are folded into one row that keeps growing in place; anything
        # without an offer_id (player trades, or older events from before offer_id existed)
        # is shown as its own row exactly as before.
        rows: list[tuple[SyncedTrade, tuple[str, ...]]] = []
        offer_row_index: dict[str, int] = {}
        for trade in trades:
            offer_id = trade.metadata.get("offer_id") if trade.event_type == "ge_fill" else None
            index = offer_row_index.get(offer_id) if isinstance(offer_id, str) and offer_id else None
            if index is None:
                if isinstance(offer_id, str) and offer_id:
                    offer_row_index[offer_id] = len(rows)
                rows.append((trade, (trade.event_id,)))
            else:
                # `trades` is newest-first, so the row already at `index` is always the more
                # recent side of the merge and its occurred_at/event_id stay the display values.
                existing_trade, existing_ids = rows[index]
                rows[index] = (
                    _merge_synced_trades(existing_trade, trade),
                    existing_ids + (trade.event_id,),
                )
        # Keyed by the row's displayed event_id rather than its position: this table is
        # sortable, so a visual row index stops matching this order the moment the user
        # clicks a header — and acting on the wrong key here deletes the wrong trade.
        self._synced_trade_rows = {trade.event_id: (trade, ids) for trade, ids in rows}
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
            for trade, _event_ids in rows
        ]
        self._fill_table(
            self.synced_trade_table,
            values,
            green_columns=set(),
            row_ids=[trade.event_id for trade, _event_ids in rows],
        )
        self._activity_selection_changed()

    def _selected_synced_trade(self) -> tuple[SyncedTrade, tuple[str, ...]] | None:
        row = self.synced_trade_table.currentRow()
        if row < 0 or row >= self.synced_trade_table.rowCount():
            return None
        event_id = self.synced_trade_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(event_id, str):
            return None
        return self._synced_trade_rows.get(event_id)

    def _open_selected_synced_trade(self) -> None:
        selected = self._selected_synced_trade()
        if selected is None:
            QMessageBox.information(self, "No activity selected", "Select a RuneLite activity row first.")
            return
        trade, _event_ids = selected
        SyncedTradeDetailsDialog(trade, self).exec()

    def _delete_selected_synced_trade(self) -> None:
        selected = self._selected_synced_trade()
        if selected is None:
            QMessageBox.information(self, "No activity selected", "Select a RuneLite activity row first.")
            return
        _trade, event_ids = selected
        prompt = (
            "Remove this imported trade from the journal? This does not affect RuneLite or the game."
            if len(event_ids) == 1
            else (
                f"Remove all {len(event_ids)} imported fills behind this row from the journal? "
                "This does not affect RuneLite or the game."
            )
        )
        answer = QMessageBox.question(
            self,
            "Delete imported activity",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            for event_id in event_ids:
                self._journal.delete_synced_trade(event_id)
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
        tracked = [
            self._journal.track(
                candidate.item_id,
                candidate.name,
                candidate.suggested_quantity,
                candidate.buy_price,
                candidate.sell_price,
                self.strategy.currentText(),
            )
            for candidate in self._portfolio
        ]
        self._render_journal()
        self.nav.setCurrentRow(self._journal_page_index())
        # This page throws the player somewhere they were not looking, at a table that
        # may already hold fifty rows. The same blink that says "this one just filled"
        # says "these are the ones you asked for" just as well.
        self._flash_journal_rows(tracked)

    def _track_candidate(self, candidate: FlipCandidate) -> None:
        position_id = self._journal.track(
            candidate.item_id,
            candidate.name,
            candidate.suggested_quantity,
            candidate.buy_price,
            candidate.sell_price,
            self.strategy.currentText(),
        )
        self._render_journal()
        self.nav.setCurrentRow(self._journal_page_index())
        self._flash_journal_rows([position_id])

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

    def _export_journal_csv(self) -> None:
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        default_name = f"osrs-toolkit-journal-{today}.csv"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Export Trade Journal", default_name, "CSV files (*.csv)"
        )
        if not path:
            return
        content = journal_csv(self._journal.list_tracked(), self._journal.list_all())
        try:
            # utf-8-sig so Excel reads the file's em dashes and other non-ASCII text
            # correctly instead of mangling them; newline="" leaves the csv module's own
            # line endings alone rather than letting text-mode writing double them up.
            Path(path).write_text(content, encoding="utf-8-sig", newline="")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", f"Could not write the file: {exc}")
            return
        QMessageBox.information(self, "Export complete", f"Journal exported to {path}")

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
                row.notes or "Rate estimate",
                "Open guide ↗",
            ]
            for row in rows
        ]
        already_sorted = bool(self.skill_table.property("columnsInitialized"))

        def decorate_row(row_index: int) -> None:
            row = rows[row_index]
            guide_cell = self.skill_table.item(row_index, 11)
            guide_cell.setData(Qt.ItemDataRole.UserRole, row.guide_url)
            guide_cell.setToolTip(
                f"{row.notes or 'Actions per hour are practical estimates.'}\n"
                "Click to open the relevant OSRS Wiki training guide."
            )
            guide_cell.setForeground(QColor(self._link_color))
            link_font = guide_cell.font()
            link_font.setUnderline(True)
            guide_cell.setFont(link_font)

        self._fill_table(
            self.skill_table, values, green_columns={5, 7}, decorate=decorate_row
        )
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
        if column != _SKILL_GUIDE_COLUMN:
            return
        guide_url = self.skill_table.item(row, column).data(Qt.ItemDataRole.UserRole)
        if isinstance(guide_url, str) and guide_url.startswith(
            "https://oldschool.runescape.wiki/"
        ):
            QDesktopServices.openUrl(QUrl(guide_url))

    def _render_pvm(self) -> None:
        snapshot = self._loadout_snapshot
        if snapshot is None:
            self.pvm_status.setText(
                "No synced gear yet. Enable “Sync gear and bank for PvM readiness” in "
                "the RuneLite plugin settings, then open your bank in-game — the toolkit picks "
                "it up on the next RuneLite activity import."
            )
        else:
            self.pvm_status.setText(
                f"Synced for {snapshot.account_name} • last updated "
                f"{_display_timestamp(snapshot.captured_at)}."
            )
        results = assess_all(snapshot)
        estimates = [
            estimate_gp_per_hour(result.activity, self._mappings, self._points) for result in results
        ]
        order = sorted(
            range(len(results)),
            key=lambda index: (not results[index].is_ready, -estimates[index].net_gp_per_hour),
        )
        results = [results[index] for index in order]
        estimates = [estimates[index] for index in order]
        values = [
            [
                result.activity.name,
                "Ready" if result.is_ready else "Not ready" if result.assessed else "Unknown",
                ", ".join(
                    f"{missing.skill} {missing.current_level}/{missing.required_level}"
                    for missing in result.missing_skills
                )
                or "—",
                ", ".join(result.missing_gear) or "—",
                _gp(estimate.net_gp_per_hour),
                result.activity.notes or "—",
                "Open guide ↗",
            ]
            for result, estimate in zip(results, estimates, strict=True)
        ]

        def decorate_row(row_index: int) -> None:
            result = results[row_index]
            estimate = estimates[row_index]
            status_cell = self.pvm_table.item(row_index, 1)
            if result.assessed:
                status_cell.setForeground(
                    QColor(self._profit_color if result.is_ready else self._loss_color)
                )
            else:
                # Nothing was checked, so this reads as neither a pass nor a failure.
                status_cell.setForeground(QColor(self._muted_color))
                status_cell.setToolTip(
                    "Nothing is known about this account's gear or levels yet, so no "
                    "requirement here has been checked either way."
                )
            gp_cell = self.pvm_table.item(row_index, 4)
            gp_cell.setToolTip(
                f"Community loot-value estimate: {_gp(estimate.gross_gp_per_hour)}\n"
                f"Live supply cost: −{_gp(estimate.supply_cost_hour)}"
                if estimate.priced
                else f"Community loot-value estimate: {_gp(estimate.gross_gp_per_hour)}\n"
                "Live supply prices unavailable — showing the loot-value estimate alone."
            )
            guide_cell = self.pvm_table.item(row_index, 6)
            guide_cell.setData(Qt.ItemDataRole.UserRole, result.activity.wiki_url)
            guide_cell.setToolTip(f"{result.activity.notes}\nDouble-click to open the OSRS Wiki page.")
            link_font = guide_cell.font()
            link_font.setUnderline(True)
            guide_cell.setFont(link_font)
            guide_cell.setForeground(QColor(self._link_color))

        self._fill_table(self.pvm_table, values, green_columns={4}, decorate=decorate_row)

    def _open_pvm_guide(self, row: int, column: int) -> None:
        if column != _PVM_GUIDE_COLUMN:
            return
        guide_url = self.pvm_table.item(row, column).data(Qt.ItemDataRole.UserRole)
        if isinstance(guide_url, str) and guide_url.startswith("https://oldschool.runescape.wiki/"):
            QDesktopServices.openUrl(QUrl(guide_url))

    def _fill_table(
        self,
        table: QTableWidget,
        values: list[list[str]],
        green_columns: set[int],
        row_ids: list[int | str] | None = None,
        decorate: Callable[[int], None] | None = None,
    ) -> None:
        """Populate a table, optionally decorating each row by its source index.

        ``decorate`` runs while sorting is still suspended, so its row index always means
        "the nth entry the caller passed in". Decorating after this method returns is a
        bug: re-enabling sorting below immediately re-sorts by whatever indicator the user
        last clicked, after which visual row order no longer matches the caller's order.
        """
        table.setSortingEnabled(False)
        table.setRowCount(len(values))
        text_columns = table.property("textColumns") or set()
        for row_index, row in enumerate(values):
            for column, value in enumerate(row):
                cell = SortableTableItem(value)
                cell.setToolTip(value)
                if column == 0 and row_ids is not None:
                    cell.setData(Qt.ItemDataRole.UserRole, row_ids[row_index])
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    if column == 0 or column in text_columns
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if column in green_columns:
                    numeric = _leading_number(value)
                    if numeric > 0:
                        cell.setForeground(QColor(self._profit_color))
                    elif numeric < 0:
                        cell.setForeground(QColor(self._loss_color))
                table.setItem(row_index, column, cell)
        if decorate is not None:
            for row_index in range(len(values)):
                decorate(row_index)
        if values and not table.property("columnsInitialized"):
            is_responsive = isinstance(table, ResponsiveTableWidget)
            if is_responsive:
                table.begin_bulk_resize()
            table.resizeColumnsToContents()
            minimum_widths = table.property("minimumColumnWidths") or {}
            maximum_widths = table.property("maximumColumnWidths") or {}
            preferred_widths: list[int] = []
            for column in range(table.columnCount()):
                minimum = minimum_widths.get(column, 0)
                # resizeColumnsToContents() measures the text alone; the stylesheet's 7px
                # of cell padding a side is on top of that, and without covering it a
                # figure sized to the pixel comes out as "1,900,000 …".
                width = max(table.columnWidth(column), minimum) + CELL_PADDING_WIDTH
                # A free-text column (notes, assumptions, missing gear) sizes itself to its
                # single longest row, which on its own can be wider than the whole viewport
                # and push every column after it off the right edge. Capping it keeps the
                # rest of the table on screen; the full text stays in the cell's tooltip.
                width = max(
                    min(width, maximum_widths.get(column, DEFAULT_MAXIMUM_COLUMN_WIDTH)),
                    minimum,
                )
                table.setColumnWidth(column, width)
                preferred_widths.append(width)
            if is_responsive:
                table.end_bulk_resize()
                table.set_preferred_widths(preferred_widths)
            table.setProperty("columnsInitialized", True)
        table.setSortingEnabled(True)

    def _open_market_item(self, table: QTableWidget, row: int) -> None:
        """The market breakdown for a row, from whichever table asked.

        The table arrives as an argument rather than through ``sender()`` because this is
        now reached four ways — double-click, Enter, a row menu, and the watchlist's own
        page — and only the first two of those have a sender to ask.
        """
        item_id = self._row_item_id(table, row)
        if item_id is None:
            return
        mapping = self._mappings.get(item_id)
        point = next((candidate for candidate in self._points if candidate.item_id == item_id), None)
        if mapping is None or point is None:
            return
        flip = next((candidate for candidate in self._flips if candidate.item_id == item_id), None)
        dialog = ItemDetailsDialog(
            mapping, point, flip, item_id in self._watchlist, self._market_client, self
        )
        dialog.exec()
        if dialog.track_requested and flip is not None:
            self._track_candidate(flip)
        self._set_watched(item_id, dialog.watched)

    def _apply_theme(self, theme: str) -> None:
        palette = _PALETTES.get(theme, _PALETTES["Dark"])
        self._profit_color = palette.profit
        self._loss_color = palette.loss
        self._muted_color = palette.muted
        self._text_color = palette.text
        self._link_color = palette.link
        self._journal_status_colors = {
            "Planned": palette.muted,
            "Pending buy": palette.pending,
            "Bought": palette.bought,
            "Listed for sale": palette.listed,
            "Partially sold": palette.partial,
            "Completed": palette.profit,
            "Completed (manual)": palette.profit,
            "Cancelled": palette.muted,
            "Supplies": palette.muted,
        }
        # Shares "Bought"'s amber, already this app's color for "waiting on a user action".
        self._warning_color = palette.bought
        self._flash_color = palette.flash
        self._flash_row_color = palette.flash_row
        self._update_journal_badge()
        # The game draws every interface with hard pixel edges, so the theme dressed as one
        # squares off the corners the modern themes round.
        card, panel, control, bar, chunk = (
            ("0", "0", "0", "0", "0")
            if palette.square_corners
            else ("10px", "8px", "7px", "6px", "4px")
        )
        spin_up_icon = _resource_path(f"assets/spin-up{palette.icon_variant}.svg").as_posix()
        spin_down_icon = _resource_path(f"assets/spin-down{palette.icon_variant}.svg").as_posix()
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {palette.background}; color: {palette.text}; font-size: 13px; }}
            #sidebar {{ background: {palette.sidebar}; border-right: 1px solid {palette.border}; }}
            #brandCard {{ background: {palette.field}; border: 1px solid {palette.border}; border-radius: {card}; }}
            #brandTitle {{ color: {palette.text}; font-size: 16px; font-weight: 700; }}
            #brandSubtitle, #versionLabel {{ color: {palette.muted}; font-size: 11px; }}
            #title {{ font-size: 28px; font-weight: 650; margin-top: 12px; }}
            #muted, #status {{ color: {palette.muted}; }}
            #recommendation {{ background: {palette.field}; color: {palette.text}; border: 1px solid {palette.border}; border-radius: {panel}; padding: 14px; }}
            /* Descendants only. Most #recommendation cards are the label itself and keep
               their background; these two are frames holding labels, which would otherwise
               repaint the window colour over the card as a block behind their text. */
            #recommendation QLabel, #brandCard QLabel {{ background: transparent; }}
            #summaryCard {{ background: {palette.field}; color: {palette.text}; border: 1px solid {palette.border}; border-radius: {panel}; padding: 14px; font-size: 14px; }}
            #summaryCard[moneyState="positive"] {{ color: {palette.profit}; }}
            #summaryCard[moneyState="negative"] {{ color: {palette.loss}; }}
            #geSlot {{ background: {palette.field}; border: 1px solid {palette.border}; border-radius: {panel}; min-height: 86px; }}
            #geSlot[slotState="empty"] {{ background: transparent; border: 1px dashed {palette.border}; }}
            #geSlot[slotState="buy"] {{ border-color: {palette.link}; }}
            #geSlot[slotState="sell"] {{ border-color: {palette.bought}; }}
            #geSlot[slotState="collect"] {{ border-color: {palette.profit}; }}
            /* Each of these sits on the card, so it has to let the card's colour through
               rather than repainting the window background as a block behind its text. */
            #geSlotNumber, #geSlotStatus, #geSlotItem, #geSlotPrice {{ background: transparent; }}
            #geSlotNumber {{ color: {palette.muted}; font-size: 11px; font-weight: 700; }}
            #geSlot[slotState="buy"] #geSlotNumber {{ color: {palette.link}; }}
            #geSlot[slotState="sell"] #geSlotNumber {{ color: {palette.bought}; }}
            #geSlot[slotState="collect"] #geSlotNumber {{ color: {palette.profit}; }}
            #geSlotStatus {{ color: {palette.muted}; font-size: 11px; }}
            #geSlot[slotState="collect"] #geSlotStatus {{ color: {palette.profit}; font-weight: 700; }}
            #geSlotItem {{ color: {palette.text}; font-size: 13px; font-weight: 650; }}
            #geSlot[slotState="empty"] #geSlotItem {{ color: {palette.muted}; font-weight: 400; }}
            #geSlotPrice {{ color: {palette.muted}; font-size: 11px; }}
            #geSlotProgress {{ background: {palette.header}; border: 0; border-radius: {chunk}; }}
            #geSlotProgress::chunk {{ background: {palette.bought}; border-radius: {chunk}; }}
            #geSlot[slotState="buy"] #geSlotProgress::chunk {{ background: {palette.link}; }}
            #geSlot[slotState="collect"] #geSlotProgress::chunk {{ background: {palette.profit}; }}
            /* The attention blink, last so it wins on the "on" beat: these carry the same
               specificity as the slotState rules above, and Qt settles a tie by source
               order. Everything it overrides comes straight back when the property clears. */
            #geSlot[flash="on"] {{ background: {palette.flash_row}; border: 1px solid {palette.flash}; }}
            #geSlot[flash="on"] #geSlotNumber, #geSlot[flash="on"] #geSlotStatus, #geSlot[flash="on"] #geSlotItem {{ color: {palette.flash}; }}
            #geSlot[flash="on"] #geSlotProgress::chunk {{ background: {palette.flash}; }}
            #nav {{ background: transparent; border: 0; outline: 0; padding-top: 16px; }}
            #nav::item {{ padding: 11px 12px; border-radius: {control}; margin: 1px 2px; }}
            #nav::item:selected {{ background: {palette.header}; color: {palette.nav_selected_text}; }}
            QLineEdit, QSpinBox, QComboBox {{ background: {palette.field}; border: 1px solid {palette.border}; border-radius: {control}; padding: 7px 10px; min-height: 22px; selection-background-color: {palette.text_selection}; }}
            QSpinBox {{ padding-right: 62px; }}
            QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 29px; border-left: 1px solid {palette.border}; border-bottom: 1px solid {palette.border}; border-top-right-radius: {control}; }}
            QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 29px; border-left: 1px solid {palette.border}; border-top: 1px solid {palette.border}; border-bottom-right-radius: {control}; }}
            QSpinBox::up-arrow {{ image: url("{spin_up_icon}"); width: 12px; height: 8px; }}
            QSpinBox::down-arrow {{ image: url("{spin_down_icon}"); width: 12px; height: 8px; }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {palette.header}; }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {palette.focus}; }}
            QComboBox::drop-down {{ border: 0; width: 30px; }}
            QCheckBox {{ spacing: 8px; min-height: 36px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; }}
            QPushButton {{ background: {palette.accent}; color: {palette.on_accent}; border: {palette.button_border}; border-radius: {control}; padding: 10px 18px; font-weight: 650; }}
            QPushButton:hover {{ background: {palette.accent_hover}; }}
            QPushButton#secondary, QPushButton#settingsButton, QPushButton#refreshButton {{ background: {palette.header}; color: {palette.text}; border: 1px solid {palette.border}; }}
            QPushButton#secondary:hover, QPushButton#settingsButton:hover, QPushButton#refreshButton:hover {{ border-color: {palette.focus}; }}
            QPushButton:disabled {{ background: {palette.disabled_bg}; color: {palette.disabled_text}; }}
            QTableWidget {{ background: {palette.background}; alternate-background-color: {palette.field}; border: 1px solid {palette.border}; border-radius: {panel}; gridline-color: {palette.border}; }}
            QHeaderView::section {{ background: {palette.header}; color: {palette.text}; border: 0; border-bottom: 1px solid {palette.border}; padding: 10px; }}
            QTableWidget::item {{ padding: 7px; }}
            QTableWidget::item:hover {{ background: {palette.header}; }}
            QTableWidget::item:selected {{ background: {palette.selection_bg}; color: {palette.selection_text}; }}
            QScrollBar:vertical {{ background: {palette.background}; width: 12px; margin: 0; border-radius: {bar}; }}
            QScrollBar:horizontal {{ background: {palette.background}; height: 12px; margin: 0; border-radius: {bar}; }}
            QScrollBar::handle {{ background: {palette.border}; border-radius: {bar}; min-height: 30px; min-width: 30px; }}
            QScrollBar::handle:hover {{ background: {palette.muted}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            /* Unstyled, tooltips came from the OS in its own colours — pale text on pale
               grey over a dark window, and no padding to lift it off its own edge. */
            QToolTip {{ background: {palette.header}; color: {palette.text}; border: 1px solid {palette.border}; border-radius: {control}; padding: 8px 10px; font-size: 12px; }}
        """)
        self._render_flips()
        self._render_alch()
        self._render_watchlist()
        self._render_journal()
        self._render_skilling()
        self._render_pvm()

    def _run_startup_notices(self) -> None:
        """Say what changed in this version, then look for a newer one."""
        self._show_whats_new_if_updated()
        self._start_startup_update_check()

    def _show_whats_new_if_updated(self) -> None:
        """Show the release notes once, on the first run of a version."""
        last_seen = str(QSettings().value(LAST_SEEN_VERSION_KEY, ""))
        if last_seen == __version__:
            return
        # Recorded before the window opens so a failure here cannot make it reappear
        # on every launch.
        QSettings().setValue(LAST_SEEN_VERSION_KEY, __version__)
        # A first run has no version to catch up from, so it announces only what it is,
        # rather than opening a new install onto a history of releases it never ran.
        notes = current_release_notes(since=last_seen, limit=5 if last_seen else 1)
        if not notes:
            return
        WhatsNewDialog(notes, self).exec()

    def _start_startup_update_check(self) -> None:
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        thread = QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._startup_update_check_finished)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        # A failed check at start-up stays quiet: it is usually a missing network
        # connection, and Settings offers the check again with a visible result.
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._clear_update_worker)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _startup_update_check_finished(self, release: ReleaseInfo) -> None:
        if not is_newer_version(release.version, __version__):
            return
        if str(QSettings().value(SKIPPED_VERSION_KEY, "")) == release.version:
            return
        UpdateAvailableDialog(release, self).exec()

    def _clear_update_worker(self) -> None:
        if self._update_thread is not None:
            self._update_thread.deleteLater()
        self._update_worker = None
        self._update_thread = None

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        # The recommendation's ceiling comes from the window's height, and a purely
        # vertical resize never reaches the card itself. __init__ resizes before building
        # the UI, so the first of these can arrive with no card to measure.
        rows = getattr(self, "flip_recommendation_rows", None)
        if rows is not None:
            rows.fit()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        # Coming back to the window, or up from the taskbar, is the moment a flash held
        # while the player was in-game finally has an audience.
        if event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        ):
            self._release_pending_flashes()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        QSettings().setValue(WINDOW_GEOMETRY_KEY, self.saveGeometry())
        self._journal_flasher.stop()
        self._slot_flasher.stop()
        for thread in (self._thread, self._account_thread, self._update_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2_000)
        super().closeEvent(event)


class _JournalRow(NamedTuple):
    """A journal table row: the cells to show, and what decorating them needs.

    ``display_status`` is what the Status cell reads; ``raw_status`` is the status stored
    against the record. They differ when a plan with no offer placed for it reads
    "Planned" — the buttons act on the stored status, so a row has to carry both.
    """

    cells: list[str]
    record_id: int
    display_status: str
    raw_status: str
    profit: JournalPLPresentation
    needs_attention: bool


_JOURNAL_STATUS_ORDER: dict[str, int] = {
    "Planned": 0,
    "Pending buy": 0,
    "Bought": 1,
    "Listed for sale": 2,
    "Partially sold": 3,
    "Completed": 4,
    "Completed (manual)": 4,
    "Cancelled": 5,
    "Supplies": 6,
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


def confidence_standing(confidence: int, floor: int) -> float:
    """Where a score sits between the strategy's own minimum and a perfect 100.

    Every row on screen already cleared ``floor`` — that is what the filter did — so the
    raw number cannot say much on its own: 58% is a poor showing under Overnight's 65 and a
    comfortable one under Quick's 45. Scored against the floor it was actually judged by, 0
    means "scraped in" and 1 means "as good as this gets".
    """
    span = max(1, 100 - floor)
    return max(0.0, min(1.0, (confidence - floor) / span))


def _ask_price(trade: TrackedTrade, live_sell_price: int | None) -> tuple[int, bool]:
    """What a "Bought" row should tell the player to ask, and whether that number is sound.

    ``trade.sell_suggestion`` is frozen at whenever the flip was planned — for a Quick or
    Balanced strategy that never revisits it, that can be hours before the buy actually
    filled, and the market has had that whole time to move. The live passive sell target
    is what the Grand Exchange will actually support right now, so it wins whenever there
    is one to prefer; the frozen suggestion is only what is left for an item with no
    current market point at all.

    "Sound" means the chosen number clears what was actually paid, after tax — a plan made
    before a price crash, or a position auto-created with nothing better than its own buy
    price to fall back on (see ``TrackedTrade.asking_price``), can offer nothing but a
    guaranteed loss. Presenting that number in the same confident amber as a real one is
    how a cue meant to help pointed someone at their own buy price as if it were advice.
    """
    price = live_sell_price if live_sell_price is not None else trade.sell_suggestion
    if trade.actual_buy is None:
        return price, True
    return price, price - ge_tax(price) > trade.actual_buy


def _attention_tooltip(asking: int, live_sell_price: int) -> str:
    """Why a journal row is flagged, in lines short enough to read at a glance.

    Broken across three of them deliberately. Qt renders a plain-text tooltip on a single
    line however long it is, and the one sentence this used to be stretched most of the
    window — laid over the rows underneath it, which is exactly where the eye was looking.
    """
    drop_pct = (asking - live_sell_price) / asking * 100
    return (
        "This ask looks stale.\n"
        f"Asking {_gp(asking)} · market now suggests {_gp(live_sell_price)}"
        f" ({drop_pct:.1f}% lower).\n"
        "Unlikely to fill here — relist nearer the suggestion."
    )


def _percent(value: float | None, *, signed: bool = False) -> str:
    """A percentage that never rounds a real result away to a bare "-0.0%"."""
    if value is None:
        return "—"
    places = 2 if value and abs(value) < 0.05 else 1
    return f"{value:+.{places}f}%" if signed else f"{value:.{places}f}%"


def _hold_time(hours: float | None) -> str:
    """A duration at a readable scale: minutes for quick flips, days for overnight ones."""
    if hours is None:
        return "—"
    if hours < 1:
        return f"{round(hours * 60):,} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def _group_row(group: GroupPerformance, *, hold: bool) -> list[str]:
    row = [
        group.label,
        f"{group.positions:,}",
        _percent(group.win_rate),
        _signed_gp(group.realized_profit),
        _percent(group.return_on_capital),
        _gp(group.capital_traded),
    ]
    if hold:
        row.append(_hold_time(group.median_hold_hours))
    return row


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


def _format_countdown(seconds: int) -> str:
    """Hours-and-minutes countdown for the buy-limit "resets in" column, precise enough to
    be useful against a 4-hour window without needing seconds."""
    if seconds <= 0:
        return "any moment"
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_goal_percent(percent: float) -> str:
    """Rounding a real but tiny sliver of progress straight to "0%" reads as no progress
    at all against a large target — distinct from an actual 0%."""
    if 0 < percent < 1:
        return "<1%"
    return f"{percent:.0f}%"


def _format_eta(days: float) -> str:
    """A savings goal's ETA is only ever a rough projection, so it's shown in whatever
    unit keeps it readable — "~19,671 days" doesn't parse at a glance the way "~54 years"
    does, and small values stay in days where that's still the natural unit."""
    if days < 1:
        return "less than a day"
    if days < 365:
        return f"~{days:.0f} days"
    years = days / 365
    return f"~{years:.0f} years" if years >= 10 else f"~{years:.1f} years"


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


def _merge_synced_trades(recent: SyncedTrade, older: SyncedTrade) -> SyncedTrade:
    """Combine two fills of the same GE offer into one, keeping the more recent event's
    identity (event_id, occurred_at) and summing quantities/values item by item, with the
    unit price recomputed as a quantity-weighted average across both fills."""
    combined: dict[tuple[str, int], SyncedItem] = {}
    for item in (*older.items, *recent.items):
        key = (item.flow, item.item_id)
        existing = combined.get(key)
        if existing is None:
            combined[key] = item
            continue
        total_quantity = existing.quantity + item.quantity
        total_value = existing.total_value + item.total_value
        combined[key] = SyncedItem(
            flow=item.flow,
            item_id=item.item_id,
            item_name=item.item_name,
            quantity=total_quantity,
            unit_value=round(total_value / total_quantity) if total_quantity else 0,
        )
    return SyncedTrade(
        event_id=recent.event_id,
        occurred_at=recent.occurred_at,
        event_type=recent.event_type,
        account_hash=recent.account_hash,
        account_name=recent.account_name,
        counterparty=recent.counterparty,
        direction=recent.direction,
        metadata=recent.metadata,
        items=tuple(combined.values()),
    )


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
    tokens = cleaned.removeprefix("Est. ").removeprefix("⚠ ").split()
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
