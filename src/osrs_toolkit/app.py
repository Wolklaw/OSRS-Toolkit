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
    FLIP_CLOSED_STATUS,
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
from osrs_toolkit.csv_import import CsvImportError, parse_journal_csv, summarise
from osrs_toolkit.formatting import (
    attention_tooltip as _attention_tooltip,
)
from osrs_toolkit.formatting import (
    availability as _availability,
)
from osrs_toolkit.formatting import (
    compact_items as _compact_items,
)
from osrs_toolkit.formatting import (
    display_timestamp as _display_timestamp,
)
from osrs_toolkit.formatting import (
    format_countdown as _format_countdown,
)
from osrs_toolkit.formatting import (
    format_eta as _format_eta,
)
from osrs_toolkit.formatting import (
    format_goal_percent as _format_goal_percent,
)
from osrs_toolkit.formatting import (
    gp as _gp,
)
from osrs_toolkit.formatting import (
    group_row as _group_row,
)
from osrs_toolkit.formatting import (
    hold_time as _hold_time,
)
from osrs_toolkit.formatting import (
    percent as _percent,
)
from osrs_toolkit.formatting import (
    short_duration as _short_duration,
)
from osrs_toolkit.formatting import (
    signed_gp as _signed_gp,
)
from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.journal import (
    UNCHANGED,
    JournalRepository,
    TrackedTrade,
)
from osrs_toolkit.journal_mirror import JournalMirror, MirrorResult
from osrs_toolkit.journal_presentation import (
    JOURNAL_STATUS_FILTERS,
    PERIOD_FILTERS,
    PLANNED_STATUS,
    JournalPLPresentation,
    journal_display_status,
    journal_pl_presentation,
    journal_status_matches,
    live_price_highlights,
    tracked_position_within_period,
    trade_needs_attention,
    trade_within_period,
)
from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.performance import (
    CalibrationRow,
    by_item,
    by_strategy,
    calibration,
    realized_results,
    summarize,
)
from osrs_toolkit.pvm import assess_all, estimate_gp_per_hour
from osrs_toolkit.ranking import (
    STRATEGIES,
    confidence_standing,
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
    FILLED_OFFER_STATES,
    GE_SLOT_COUNT,
    GEOfferScreen,
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
    find_install,
    is_newer_version,
    start_installer,
)
from osrs_toolkit.web_source import (
    DEFAULT_BASE_URL,
    INTERACTIVE_TIMEOUT_SECONDS,
    POLL_TIMEOUT_SECONDS,
    ToolkitWebClient,
    ToolkitWebError,
    WebAppSource,
)

# How often the journal is checked against the website.
MIRROR_INTERVAL_MS = 60 * 1_000

# Slower poll while the window isn't focused.
MIRROR_BACKGROUND_INTERVAL_MS = 5 * 60 * 1_000

WEB_BASE_URL_KEY = "web/base_url"
WEB_TOKEN_KEY = "web/token"


def configured_web_client(timeout: float | None = None) -> ToolkitWebClient:
    """A client for whatever website this app has been pointed at.

    The token is stored in ``QSettings`` (Windows registry) as plain text -- there's no
    system keychain dependency here. It only grants this account's journal, and can be
    revoked immediately from the website's Profile page.
    """
    base_url = str(QSettings().value(WEB_BASE_URL_KEY, DEFAULT_BASE_URL) or "")
    token = str(QSettings().value(WEB_TOKEN_KEY, "") or "")
    return ToolkitWebClient(base_url, token, timeout)


def build_sync_importer() -> RuneLiteSyncImporter:
    """Where this app reads live plugin state from: the website, always.

    Never falls back to the local ``.runelite`` folder, even with no token configured --
    that's the same-machine plugin arrangement the Plugin Hub rejected. An unconfigured
    client just answers "nothing yet"; ``RuneLiteConnectionDialog`` prompts for the
    missing token instead.

    Given the short poll timeout rather than the 20s default: this source is read from the
    GUI thread on a 3-second timer, so that default is how long the window could sit frozen
    on one stalled request.
    """
    return RuneLiteSyncImporter(source=WebAppSource(configured_web_client(POLL_TIMEOUT_SECONDS)))


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
        # Without a floor, this is the widget that gets squeezed first as the window shrinks.
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

# Cap on content-measured column width, so a long notes column can't push other columns off screen.
DEFAULT_MAXIMUM_COLUMN_WIDTH = 320

# Below this, a column holds a short label/figure and isn't worth squeezing further.
NARROW_COLUMN_WIDTH = 160

# Stylesheet per-cell padding (7px a side) plus a little slack.
CELL_PADDING_WIDTH = 24
# Thirds of the room between a strategy's minimum and 100 — see confidence_standing.
_CONFIDENCE_FAIR = 1 / 3
_CONFIDENCE_STRONG = 2 / 3

# Kept apart from column 0's UserRole, which holds a tracked position id or manual trade id
# from a different numbering. Only tracked rows carry this.
_FLASH_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
# Positions with goods actually on the Grand Exchange, so a live offer's price is theirs.
_LISTED_STATUSES = frozenset({"Listed for sale", "Partially sold"})

# Doubled ampersand is Qt's escape for a literal "&" in a tab title (a single & underlines
# the next letter as an Alt shortcut).
_PLANS_TAB_TITLE = "Plans && completed"
_PLANS_TAB_INDEX = 0

# The Guide column on the Skilling Profit / PvM Readiness tables -- carries a wiki URL, the
# only cell a double-click opens.
_SKILL_GUIDE_COLUMN = 11
_PVM_GUIDE_COLUMN = 6

LAST_SEEN_VERSION_KEY = "app/last_seen_version"
SKIPPED_VERSION_KEY = "updates/skipped_version"
WINDOW_GEOMETRY_KEY = "window/geometry"

# How much of a restored window has to land on an attached monitor to be worth keeping.
# Qt clamps most cases back on screen itself; this covers the rest.
_ONSCREEN_MINIMUM = 120


def current_release_notes(*, since: str = "", limit: int = 5) -> list[ReleaseNotes]:
    """Release notes for the running version plus anything released since ``since``."""
    releases = load_release_notes(_resource_path("CHANGELOG.md"))
    return catch_up_notes(releases, current=__version__, since=since, limit=limit)


class WhatsNewDialog(QDialog):
    """Headline notes for the running version and any missed before it. Full detail is a
    click away rather than shown inline."""

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
        changelog_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CHANGELOG_URL)))
        footer.addWidget(changelog_button)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Continue")
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)


class UpdateAvailableDialog(QDialog):
    """Offers a newer official release: install it now, defer it, or skip the version.

    Owns the download so both the start-up check and the manual check in Settings install
    an update the same way. An installed copy is replaced in place and reopened; a portable
    copy is instead offered the installer wizard.
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
        # Settled up front so the window can say which path (install vs. installer wizard)
        # is about to happen before the download starts.
        self._install = find_install()
        self._download_thread: QThread | None = None
        self._download_worker: QObject | None = None
        self.setWindowTitle("Update available")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>Version {release.version} is available</h2>"))
        summary = QLabel(
            f"You are running version {__version__}. The update is downloaded from the "
            "official GitHub release and its SHA-256 digest is verified before it is applied."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        note = QLabel(
            "Save any work first — this app closes, updates itself, and reopens on the new "
            "version. There is no installer to click through."
            if self._install
            else "Save any work first — this app closes when the installer opens. You are "
            "running the portable edition, so this creates a standard installed copy and "
            "leaves your portable folder unchanged.",
            objectName="muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel(objectName="recommendation")
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        self.notes_button = QPushButton("View release notes", objectName="secondary")
        self.notes_button.setEnabled(bool(release.page_url))
        self.notes_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
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
        worker.progress.connect(lambda value: self._report(f"Downloading update… {value}%"))
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
        self._report(
            "Verified. Installing the update — this window closes and the app reopens."
            if self._install
            else "Verified. Opening the installer…"
        )
        try:
            start_installer(path, self._install)
        except Exception as exc:  # noqa: BLE001 - present launch failures to the user.
            self._download_failed(str(exc))
            return
        # Setup can't replace this executable while it's running.
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
        # Let an in-flight download finish rather than leave a half-written installer behind.
        if self._download_thread is not None and self._download_thread.isRunning():
            self._report("Finishing the download — this window closes when the update starts.")
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
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _default_database_path() -> Path:
    local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
    return local_data / "OSRSToolkit" / "data" / "toolkit.db"


class _Palette(NamedTuple):
    """One theme's colours, plus the few shape choices a theme may make.

    Fields with defaults hold the common values; each theme overrides only what it changes.
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
    # Attention blink: flash is the lit-up text/edge, flash_row the tinted wash behind it
    # (a tint, not a fill, so the row's own status/P&L colour stays readable).
    flash: str = "#ffd34d"
    flash_row: str = "#4a3c14"
    # The price a live Grand Exchange offer is working on. Deliberately a different colour
    # from flash, since this holds for as long as the offer does rather than blinking briefly.
    live_offer: str = "#65a9ff"
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
        live_offer="#1f64a8",
        icon_variant="-dark",
    ),
    # Styled after the game's own interfaces: stone panels, square corners, interface
    # orange for emphasis. Profit/loss keep their own green/red regardless.
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
        live_offer="#7ab4ff",
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
        web_base_url: str = "",
        web_token: str = "",
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

        website = QWidget()
        website_layout = QVBoxLayout(website)
        website_layout.addWidget(QLabel("Website address"))
        self.web_base_url_field = QLineEdit(web_base_url or DEFAULT_BASE_URL)
        website_layout.addWidget(self.web_base_url_field)
        website_layout.addWidget(QLabel("Desktop access token"))
        self.web_token_field = QLineEdit(web_token)
        self.web_token_field.setEchoMode(QLineEdit.EchoMode.Password)
        website_layout.addWidget(self.web_token_field)
        website_note = QLabel(
            "Your RuneLite plugin sends to the website, and this app reads from it. Generate a "
            "desktop access token on the website's <b>Profile</b> page and paste it here.<br><br>"
            "Leave the token empty to keep reading the old RuneLite folder on this computer "
            "instead. That still works for an older plugin, but new plugin versions no longer "
            "write to it.",
            objectName="muted",
        )
        website_note.setWordWrap(True)
        website_layout.addWidget(website_note)
        self.web_status = QLabel("", objectName="muted")
        self.web_status.setWordWrap(True)
        website_layout.addWidget(self.web_status)
        website_actions = QHBoxLayout()
        self.check_website_button = QPushButton("Check connection", objectName="secondary")
        self.check_website_button.clicked.connect(self._check_website)
        website_actions.addWidget(self.check_website_button)
        website_actions.addStretch()
        website_layout.addLayout(website_actions)
        website_layout.addStretch()
        tabs.addTab(website, "Website")

        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_text = QLabel(
            "<h2>About OSRS Toolkit</h2>"
            f"<p><b>Version {__version__}</b></p>"
            "<p>An independent, fan-made market companion with Grand Exchange research, "
            "profit calculators, and a local trade journal.</p>"
            "<p><b>Game interaction</b><br>This toolkit does not play Old School RuneScape, "
            "generate game input, communicate with game worlds, alter network traffic, or "
            "modify the game client. Optional RuneLite sync reads trade events from your account on "
            "runescope.app, which the RuneLite plugin sends them to.</p>"
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
        self.update_status = QLabel(
            "Check GitHub for a newer official release.", objectName="muted"
        )
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
        help_text = QLabel(
            "<h2>Get help</h2>"
            "<p>Found a bug, have a feature request, or something isn't working right? "
            "Here's how to reach me:</p>"
            "<p><b>Project page</b><br>"
            "<a href='https://github.com/Wolklaw/OSRS-Toolkit'>github.com/Wolklaw/OSRS-Toolkit</a> "
            "&mdash; report an issue, check the changelog, or browse the source.</p>"
            "<p><b>Email</b><br>"
            "<a href='mailto:wolklawgaming@gmail.com'>wolklawgaming@gmail.com</a></p>"
            "<p><b>In-game</b><br>Lord Wolklaw</p>"
        )
        help_text.setWordWrap(True)
        help_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        help_text.setOpenExternalLinks(True)
        support_layout.addWidget(help_text)
        support_text = QLabel(
            "<h2>Support development</h2>"
            "<p>OSRS Toolkit is free to use. If it saves you time and you would like to support "
            "its development, you can leave an optional tip. Every feature remains available "
            "whether you tip or not.</p>"
        )
        support_text.setWordWrap(True)
        support_layout.addWidget(support_text)
        tip_button = QPushButton("Tip the developer (optional)", objectName="secondary")
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

    def _check_website(self) -> None:
        """Check the entered website/token before saving.

        Uses the interactive timeout, not the background one, so a bad address comes back
        quickly instead of freezing the window.
        """
        self.web_status.setText("Checking…")
        self.check_website_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            client = ToolkitWebClient(
                self.web_base_url_field.text().strip(),
                self.web_token_field.text().strip(),
                INTERACTIVE_TIMEOUT_SECONDS,
            )
            name = client.check()
        except ToolkitWebError as error:
            self.web_status.setText(str(error))
        else:
            self.web_status.setText(f"Connected as {name}.")
        finally:
            QApplication.restoreOverrideCursor()
            self.check_website_button.setEnabled(True)

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
        # Shows recent history regardless of whether this launch had anything new to report.
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
                f"Version {release.version} is available. It is downloaded from the official "
                "GitHub release and verified before it is applied."
            )
            self.update_button.setText(f"Download and install {release.version}")
        else:
            self.update_status.setText(f"You are up to date — version {__version__}.")
            self.update_button.setText("Check again")
        self.update_button.setEnabled(True)

    def _download_update(self, release: ReleaseInfo) -> None:
        # Skipping a version only makes sense from the start-up check, not a manual one.
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
            "Install and enable the OSRS Toolkit plugin in RuneLite, and give it a pairing "
            "token from your runescope.app profile. Player-to-player trade tracking is "
            "optional in the RuneLite plugin settings."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.status = QLabel(objectName="recommendation")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.privacy = QLabel(objectName="muted")
        self.privacy.setWordWrap(True)
        layout.addWidget(self.privacy)
        actions = QHBoxLayout()
        plugin_button = QPushButton("View RuneLite plugin")
        plugin_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.PLUGIN_URL)))
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
        reading_website = self.importer.sync_root is None
        if connection.active:
            character = f" as {connection.account_name}" if connection.account_name else ""
            player_trades = "on" if connection.player_trade_tracking else "off"
            self.status.setText(
                f"Connected{character} — new trades will sync automatically. "
                f"Player-trade tracking is {player_trades}."
            )
        elif reading_website and not self.importer.configured:
            # Distinguish "no token configured" from "plugin not installed" -- otherwise
            # someone with a working plugin gets sent to reinstall it.
            self.status.setText(
                "No desktop access token yet. Get one from your runescope.app Profile page, "
                "under Desktop app, then paste it into Settings → Website."
            )
        elif connection.detected and reading_website and not connection.source_reachable:
            self.status.setText(
                "Cannot reach the website right now — this is not the plugin's fault. Your "
                "journal is safe on this PC and will catch up once it is reachable again."
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
        # The privacy claim depends on which source is configured -- keep it accurate.
        self.privacy.setText(
            "Trade and position data reaches your runescope.app account, and nowhere else. "
            "Jagex credentials are never involved."
            if reading_website
            else "The bridge uses local files only. No Jagex credentials or trade history are "
            "sent anywhere."
        )
        self.folder_button.setVisible(not reading_website)
        self.folder_button.setEnabled(connection.detected)

    def _open_folder(self) -> None:
        if self.importer.sync_root is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.importer.sync_root)))


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


class RetagTradesDialog(QDialog):
    """Refile several positions at once: what they are, not what they hold.

    Every field starts on "Leave unchanged" and means it, so this only ever writes what was
    deliberately picked. Nothing here can touch a price, a fill or a quantity — those differ
    per position, and a batch editor that could overwrite them across a selection is a way to
    lose a day's real numbers to one wrong click.
    """

    UNCHANGED_LABEL = "Leave unchanged"
    #: Only meaningful for a batch. Marking a whole shopping trip as Supplies is the case
    #: this dialog exists for, so it leads.
    STATUSES: ClassVar[list[str]] = [
        "Supplies",
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Cancelled",
    ]

    def __init__(
        self,
        count: int,
        strategies: list[str],
        characters: list[tuple[str | None, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Update {count} trades")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        heading = QLabel(
            f"Changing <b>{count}</b> selected {'trade' if count == 1 else 'trades'}. "
            "Anything left on “Leave unchanged” is not touched.",
            objectName="muted",
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        form = QFormLayout()
        self.status = QComboBox()
        self.status.addItem(self.UNCHANGED_LABEL)
        self.status.addItems(self.STATUSES)
        form.addRow("Status", self.status)

        self.strategy = QComboBox()
        self.strategy.addItem(self.UNCHANGED_LABEL)
        self.strategy.addItems(strategies)
        form.addRow("Strategy", self.strategy)

        self.character = QComboBox()
        self.character.addItem(self.UNCHANGED_LABEL)
        for account_hash, name in characters:
            self.character.addItem(name, account_hash)
        form.addRow("Character", self.character)
        layout.addLayout(form)

        self.warning = QLabel(objectName="muted")
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        self.status.currentTextChanged.connect(self._status_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _status_changed(self, status: str) -> None:
        # Said before the click rather than reported after it: "Completed" is the one status
        # a position can fail to qualify for, and finding that out from a summary afterwards
        # reads like the app ignored you.
        self.warning.setText(
            "Only positions whose buy and sell fills already cover their full quantity can "
            "become Completed. Any that don't are left as they are."
            if status == "Completed"
            else ""
        )

    def chosen_status(self) -> str | None:
        text = self.status.currentText()
        return None if text == self.UNCHANGED_LABEL else text

    def chosen_strategy(self) -> str | None:
        text = self.strategy.currentText()
        return None if text == self.UNCHANGED_LABEL else text

    def chosen_character(self) -> str | None | object:
        """The account hash to file these under, or ``UNCHANGED``.

        ``None`` is a real answer here — "belongs to no character in particular" — so it
        cannot double as "leave alone", which is why the sentinel exists.
        """
        if self.character.currentText() == self.UNCHANGED_LABEL:
            return UNCHANGED
        return self.character.currentData()

    def changes_anything(self) -> bool:
        return (
            self.chosen_status() is not None
            or self.chosen_strategy() is not None
            or self.chosen_character() is not UNCHANGED
        )

    def _accept_if_valid(self) -> None:
        if not self.changes_anything():
            QMessageBox.information(
                self,
                "Nothing to change",
                "Pick a status, a strategy or a character to apply, or press Cancel.",
            )
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
            round(sum(quantity * price for quantity, price in fills) / bought) if bought else None
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
        average = round(sum(quantity * price for quantity, price in fills) / sold) if sold else None
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
    """A summary card that navigates to the rows behind its own number.

    Only clickable while it has something to point at -- the hand cursor is the whole
    affordance, so a card reading zero must not show one.
    """

    clicked = Signal()

    def __init__(self, text: str, **kwargs: object) -> None:
        super().__init__(text, **kwargs)
        self._live = False

    def set_live(self, live: bool) -> None:
        if live == self._live:
            return
        self._live = live
        self.setCursor(Qt.CursorShape.PointingHandCursor if live else Qt.CursorShape.ArrowCursor)

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

    Each column has a preferred width and a floor. Spare space is shared proportionally;
    a too-narrow viewport scales all columns down together, never letting one get pushed
    off the right edge.
    """

    # A row opened from the keyboard, as (row, column) to match cellDoubleClicked.
    rowActivated = Signal(int, int)

    def __init__(self, column_count: int) -> None:
        super().__init__(0, column_count)
        self._preferred_widths: list[int] = []
        self._floor_widths: list[int] = []
        self._applying_widths = False
        self._user_resized_columns: set[int] = set()
        self._tooltip_index = QModelIndex()
        self.horizontalHeader().sectionResized.connect(self._section_resized)
        # Qt's native tooltip only fires once the cursor stops moving, which is a small
        # target for a narrow glyph like the warning icon. Track every move instead and
        # show on arrival at the cell.
        self.setMouseTracking(True)

    def _section_resized(self, column: int, _old_width: int, _new_width: int) -> None:
        if not self._applying_widths:
            self._user_resized_columns.add(column)

    def viewportEvent(self, event) -> bool:  # type: ignore[no-untyped-def]
        """Suppress Qt's native tooltip; ``mouseMoveEvent`` shows it instead, on arrival."""
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
        # Anchoring to the cell's rect lets Qt hide the tooltip as soon as the cursor leaves it.
        QToolTip.showText(event.globalPosition().toPoint(), text, self, self.visualRect(index))

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().leaveEvent(event)
        self._tooltip_index = QModelIndex()
        QToolTip.hideText()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Enter opens the current row, like double-clicking it.

        Not using Qt's ``activated`` signal: on Windows it also fires on the double-click
        that ``cellDoubleClicked`` reports, which would open the row twice.
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
        """A cell's tooltip, skipped when it just repeats the visible (non-elided) text.

        A tooltip that differs from the cell text is a deliberate explanation (a P/L
        breakdown, a stale ask, etc.) and always shows.
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
        # Stylesheet pads header sections 10px a side; a sorted column also needs room for
        # its indicator.
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
        """Scale every column down together, proportionally, holding each at its floor.

        Proportional shrinking keeps the widest column on a large monitor still the widest
        on a small one. Columns that would fall under their floor are pinned there and the
        rest re-share what's left; if even the floors overflow, the table scrolls.
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

        Independent rounding can leave a few pixels over; the widest column absorbs them
        so the parts always sum back to ``amount`` exactly.
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
    """A label that elides its own text to whatever width the layout gives it.

    A plain QLabel refuses to go below the width of its text. This one keeps the full text
    as a tooltip and shows as much as fits.
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

    Grows to fit content instead of using a fixed height. ``window_reserve`` is the height
    the rest of the page needs; whatever's left over is all this may take.
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
        # Measured as if the scrollbar were always present, to avoid a resize loop where
        # the wider assumption makes a scrollbar appear, re-wrapping the text taller.
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

    Keeps only the bookkeeping (which keys are lit, and whether this is an "on" beat);
    drawing is left to whoever connects to ``pulsed``, since a table row and a GE slot
    card highlight differently. A key is whatever the caller identifies targets by.
    """

    pulsed = Signal()

    # Three full blinks, ending dark.
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

        A second offer finishing shortly after the first joins in rather than cutting the
        first one's blink short.
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

        Unlike ``start`` (which joins), this replaces -- a click on a slot card is a new
        "which row is this?" question, not an event to add to the last one.
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
        # Emitted after stopping too, so the last beat clears the highlight.
        self.pulsed.emit()


class GEOfferSlotCard(QFrame):
    """One Grand Exchange slot, laid out like the slot it mirrors in-game.

    Shows fill progress as a bar rather than a raw "31 / 116" figure.
    """

    # The item this slot holds an offer for. Zero (empty slot) is never emitted.
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
        # Terminal offers are done but not yet collected in-game -- give them their own tone.
        self.setProperty("slotState", "collect" if slot.is_terminal else slot.side or "empty")
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
        self._sync_importer = build_sync_importer()
        self._journal_mirror = JournalMirror(self._journal, configured_web_client())
        # Dedicated to on-demand item-details lookups (price history), separate from the
        # transient client MarketWorker creates for each periodic snapshot poll.
        self._market_client = WikiMarketClient()
        self._loadout_snapshot = self._journal.get_latest_loadout_snapshot()
        self._last_sync_message = ""
        self._last_mirror_message = ""
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
        self._live_offer_color = "#65a9ff"
        # The last state each renderer saw, so the next pass can tell what changed. None
        # means "nothing seen yet" -- the first look after startup seeds these silently,
        # so nothing left finished overnight blinks the moment the app opens.
        self._journal_statuses: dict[int, str] | None = None
        # Position id -> item id, for matching a queued flash against a GE slot (which
        # only knows items).
        self._journal_item_ids: dict[int, int | None] = {}
        self._ge_slot_states: dict[int, str] | None = None
        # Items currently finished-but-uncollected on the GE. None means "not read yet",
        # same convention as _ge_slot_states.
        self._ge_terminal_items: frozenset[int] | None = None
        # The character the Grand Exchange panel is drawing, held across a moment where the
        # website could not be reached -- see _synced_account_hash.
        self._last_synced_account_hash: str | None = None
        # What the Needs attention card is counting, so clicking it can show them. Newest
        # first, the order the table itself is in -- a set would hand back an arbitrary one.
        self._attention_positions: list[int] = []
        # Where the plugin says the player is in the GE interface, every buy or sale the
        # Grand Exchange has going as (item id, side), and which of each journal row's two
        # price cells that marks (resolved by _render_journal).
        self._offer_screen: GEOfferScreen | None = None
        self._live_offers: frozenset[tuple[int, str | None]] = frozenset()
        self._live_price_sides: dict[int, frozenset[str]] = {}
        # Held until the surface each points at is actually being looked at — see
        # _release_pending_flashes.
        self._pending_journal_flash: set[int] = set()
        self._pending_slot_flash: set[int] = set()
        self._journal_flasher = AttentionFlasher(self)
        self._journal_flasher.pulsed.connect(self._paint_journal_flash)
        self._slot_flasher = AttentionFlasher(self)
        self._slot_flasher.pulsed.connect(self._paint_slot_flash)
        self._market_buttons: list[QPushButton] = []
        # Buttons that act on "the selected row" -- held here so selection changes can
        # enable/disable them together, rather than each failing with "select a row first".
        self._journal_row_buttons: list[QPushButton] = []
        self._loot_log_row_buttons: list[QPushButton] = []
        self._death_log_row_buttons: list[QPushButton] = []
        # The Journal page is built first and renders itself as it is built, which reaches
        # the Performance page's renderer before its widgets exist.
        self._performance_ready = False
        self._cash_debounce = QTimer(self)
        self._cash_debounce.setSingleShot(True)
        self._cash_debounce.setInterval(200)
        self._cash_debounce.timeout.connect(self._cash_changed)
        # Built before _restore_window_geometry: restoreGeometry can fire changeEvent
        # synchronously, and changeEvent reaches for this timer.
        self._mirror_timer = QTimer(self)
        self._mirror_timer.setInterval(MIRROR_INTERVAL_MS)
        self._mirror_timer.timeout.connect(self._mirror_journal)
        self._build_ui()
        self._apply_theme(self._theme)
        self._restore_window_geometry()
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(3_000)
        self._sync_timer.timeout.connect(self._import_runelite_events)
        self._sync_timer.start()
        # self as receiver so Qt drops the pending call if the window is gone by then.
        QTimer.singleShot(100, self, self._import_runelite_events)
        QTimer.singleShot(250, self, self.load_market)
        # Let the window paint before anything modal appears in front of it.
        QTimer.singleShot(600, self, self._run_startup_notices)
        self._mirror_timer.start()
        # Delayed rather than run alongside the first import -- nothing here is urgent.
        QTimer.singleShot(4_000, self, self._mirror_journal)
        self._market_timer = QTimer(self)
        self._market_timer.setInterval(5 * 60 * 1_000)
        self._market_timer.timeout.connect(self.load_market)
        self._market_timer.start()
        # Rechecks periodically so a long-running session still learns about a release
        # published after startup, on the same quiet terms as the startup check.
        self._update_check_timer = QTimer(self)
        self._update_check_timer.setInterval(60 * 60 * 1_000)
        self._update_check_timer.timeout.connect(self._start_startup_update_check)
        self._update_check_timer.start()

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
        # Ctrl+1..7, in sidebar order. Advertised via the tooltip on each row.
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

        ``restoreGeometry`` already clamps to an attached monitor; the check below is a
        backstop for the cases it misses, falling back to ``__init__``'s default geometry.
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
        """Wire both ways of opening a row: double-click it, or select it and press Enter."""
        table.cellDoubleClicked.connect(open_row)
        table.rowActivated.connect(open_row)

    def _install_row_menu(
        self, table: ResponsiveTableWidget, build: Callable[[QMenu, int], None]
    ) -> None:
        """Right-click a row for the verbs that apply to it.

        The row is made current before ``build`` runs, so "the selected trade" always
        means the row the menu opened over. An empty menu is never shown.
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
        """A "copy this name" entry, when the row has a name to copy."""
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

        Shared by the GE Flipper, Watchlist, and Alch Finder tables -- all keyed to an item id.
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

        ``delete`` still asks for confirmation -- this just saves reaching for the button.
        """
        shortcut = QShortcut(QKeySequence.StandardKey.Delete, table)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(delete)

    def _journal_selection_changed(self) -> None:
        selected = bool(self.journal_table.selectionModel().hasSelection())
        for button in self._journal_row_buttons:
            button.setEnabled(selected)
        # The button says which editor it opens, because they are different dialogs: one row
        # gets the full editor with its fills, several get the batch one.
        count = len(self._selected_journal_rows())
        self.journal_update_button.setText(
            f"Update {count} selected trades" if count > 1 else "Update selected trade"
        )

    def _build_journal_row_menu(self, menu: QMenu, row: int) -> None:
        """The row menu for a journal entry: the two buttons above it, on the row itself."""
        count = len(self._selected_journal_rows())
        menu.addAction(
            f"Update {count} trades…" if count > 1 else "Update trade…",
            self._update_selected_trade,
        )
        menu.addAction(
            f"Delete {count} trades…" if count > 1 else "Delete trade…",
            self._delete_selected_trade,
        )
        menu.addSeparator()
        self._add_copy_action(menu, self.journal_table, row, 2)

    def _loot_log_selection_changed(self) -> None:
        selected = bool(self.loot_log_table.selectionModel().hasSelection())
        for button in self._loot_log_row_buttons:
            button.setEnabled(selected)

    def _build_loot_log_row_menu(self, menu: QMenu, row: int) -> None:
        """The row menu for an imported loot delivery."""
        menu.addAction("Delete entry…", self._delete_selected_loot_event)
        menu.addSeparator()
        self._add_copy_action(menu, self.loot_log_table, row, 1)

    def _death_log_selection_changed(self) -> None:
        selected = bool(self.death_log_table.selectionModel().hasSelection())
        for button in self._death_log_row_buttons:
            button.setEnabled(selected)

    def _build_death_log_row_menu(self, menu: QMenu, row: int) -> None:
        """The row menu for an imported death."""
        menu.addAction("Delete entry…", self._delete_selected_death_event)
        menu.addSeparator()
        self._add_copy_action(menu, self.death_log_table, row, 1)

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
        # Headline and closing note sit outside the scroll area, so a long list scrolls
        # under them rather than carrying them off the top of the card.
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
        # Reserve covers the page heading, controls, card text, buttons, and ~4 table rows.
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
            [
                "Item",
                "Buy",
                "Sell",
                "Safe max",
                "Profit ea.",
                "ROI",
                "1h volume",
                "Limit",
                "Max potential",
                "Confidence",
            ],
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
            self.watchlist_table,
            lambda row, _column: self._open_market_item(self.watchlist_table, row),
        )
        self._install_row_menu(
            self.watchlist_table, self._build_market_row_menu(self.watchlist_table)
        )
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
        self.journal_attention = ClickableCard("Needs attention\n0", objectName="summaryCard")
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
        self.journal_period_filter.currentTextChanged.connect(self._journal_period_filter_changed)
        summary.addWidget(self.journal_period_filter)
        layout.addLayout(summary)

        # The 8 GE slots, laid out like the game does. Re-rendered every 3s by
        # _import_runelite_events, so it fills as the offers do.
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
        # Only shown when a slot click needs explaining.
        self.ge_slot_hint = QLabel(objectName="muted")
        self.ge_slot_hint.setWordWrap(True)
        self.ge_slot_hint.hide()
        layout.addWidget(self.ge_slot_hint)

        self.journal_tabs = QTabWidget()
        self.journal_tabs.currentChanged.connect(lambda _index: self._release_pending_flashes())
        plans_tab = QWidget()
        plans_layout = QVBoxLayout(plans_tab)
        actions = QHBoxLayout()
        add_button = QPushButton("Add completed trade")
        add_button.clicked.connect(self._add_trade)
        self.journal_update_button = QPushButton("Update selected trade", objectName="secondary")
        self.journal_update_button.setToolTip(
            "Select several rows with Ctrl or Shift to change their status, strategy or "
            "character in one go."
        )
        update_button = self.journal_update_button
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
        import_button = QPushButton("Import CSV", objectName="secondary")
        import_button.setToolTip(
            "Read a Trade Journal CSV back in. You choose whether to add it to what is "
            "already here or replace it, and nothing is written until you confirm."
        )
        import_button.clicked.connect(self._import_journal_csv)
        actions.addWidget(add_button)
        actions.addWidget(update_button)
        actions.addWidget(delete_button)
        actions.addWidget(export_button)
        actions.addWidget(import_button)
        actions.addStretch()
        actions.addWidget(QLabel("Status", objectName="muted"))
        self.journal_status_filter = QComboBox()
        self.journal_status_filter.addItems(JOURNAL_STATUS_FILTERS)
        saved_filter = str(QSettings().value("journal/status_filter", JOURNAL_STATUS_FILTERS[0]))
        self.journal_status_filter.setCurrentText(
            saved_filter if saved_filter in JOURNAL_STATUS_FILTERS else JOURNAL_STATUS_FILTERS[0]
        )
        self.journal_status_filter.setMinimumWidth(160)
        self.journal_status_filter.setToolTip(
            "Show every journal entry or focus on one stage of the trade lifecycle. Supplies "
            "are quest or skilling buys marked out of your flip totals — select 'Update "
            "selected trade' and set the status to Supplies to move one there."
        )
        self.journal_status_filter.currentTextChanged.connect(self._journal_status_filter_changed)
        actions.addWidget(self.journal_status_filter)
        plans_layout.addLayout(actions)
        # Lived in the RuneLite activity tab until that tab was removed. Still worth showing
        # on this page: it is what says whether the fills filling this table are still
        # arriving, and _update_runelite_status has written to it all along.
        self.runelite_status = QLabel("RuneLite not connected", objectName="status")
        self.runelite_status.setWordWrap(True)
        plans_layout.addWidget(self.runelite_status)
        self.journal_filter_empty = QLabel(
            "No journal entries match this status and period filter.", objectName="status"
        )
        plans_layout.addWidget(self.journal_filter_empty)
        self.journal_table = self._table(
            [
                "Date",
                "Status",
                "Item",
                "Qty",
                "Buy suggestion",
                "Actual buy",
                "Sell suggestion",
                "Actual sell",
                "P/L",
            ],
            minimum_widths={0: 105, 1: 155, 2: 230, 8: 130},
            text_columns={1, 2},
            multi_select=True,
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

        loot_log_tab = QWidget()
        loot_log_layout = QVBoxLayout(loot_log_tab)
        loot_log_controls = QHBoxLayout()
        loot_log_remove_button = QPushButton("Delete entry", objectName="secondary")
        loot_log_remove_button.clicked.connect(self._delete_selected_loot_event)
        self._loot_log_row_buttons += [loot_log_remove_button]
        loot_log_controls.addStretch()
        loot_log_controls.addWidget(loot_log_remove_button)
        loot_log_layout.addLayout(loot_log_controls)
        self.loot_log_table = self._table(
            ["Time", "NPC", "Character", "Items", "Value"],
            minimum_widths={0: 145, 1: 140, 2: 140, 3: 260},
            text_columns={1, 2, 3},
        )
        self._install_row_menu(self.loot_log_table, self._build_loot_log_row_menu)
        self._delete_selected_row_on_delete_key(
            self.loot_log_table, self._delete_selected_loot_event
        )
        self.loot_log_table.itemSelectionChanged.connect(self._loot_log_selection_changed)
        self._loot_log_selection_changed()
        loot_log_layout.addWidget(self.loot_log_table, 1)
        self.journal_tabs.addTab(loot_log_tab, "Loot Log")

        death_log_tab = QWidget()
        death_log_layout = QVBoxLayout(death_log_tab)
        death_log_controls = QHBoxLayout()
        death_log_remove_button = QPushButton("Delete entry", objectName="secondary")
        death_log_remove_button.clicked.connect(self._delete_selected_death_event)
        self._death_log_row_buttons += [death_log_remove_button]
        death_log_controls.addStretch()
        death_log_controls.addWidget(death_log_remove_button)
        death_log_layout.addLayout(death_log_controls)
        self.death_log_table = self._table(
            ["Time", "Character", "Skulled", "Carried", "Value"],
            minimum_widths={0: 145, 1: 140, 3: 260},
            text_columns={1, 2, 3},
        )
        self._install_row_menu(self.death_log_table, self._build_death_log_row_menu)
        self._delete_selected_row_on_delete_key(
            self.death_log_table, self._delete_selected_death_event
        )
        self.death_log_table.itemSelectionChanged.connect(self._death_log_selection_changed)
        self._death_log_selection_changed()
        death_log_layout.addWidget(self.death_log_table, 1)
        self.journal_tabs.addTab(death_log_tab, "Death Log")

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
        self._render_loot_log()
        self._render_death_log()
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
        # _render_journal fires while the Journal page is still being built.
        if not self._performance_ready:
            return
        period = self.performance_period_filter.currentText()
        # Local, not UTC, so "Today" means the user's day.
        now = datetime.now().astimezone()
        tracked = [trade for trade in self._journal.list_tracked() if trade.status != "Supplies"]
        results = realized_results(tracked, self._journal.list_all(), period, now)

        summary = summarize(results)
        self.performance_profit.setText(f"Realized profit\n{_signed_gp(summary.realized_profit)}")
        self._set_money_state(self.performance_profit, summary.realized_profit)
        self.performance_return.setText(f"Return on capital\n{_percent(summary.return_on_capital)}")
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
        """The persisted goal, or None if none is set. Stored in QSettings, not the
        journal database, since it's app configuration rather than trade history."""
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
            # Every cell in the row carries the caveat, not just one column.
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
        self.alch_safety.setCurrentText(saved_policy if saved_policy in ALCH_POLICIES else "Safer")
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
        # Enter opens the guide from anywhere on the row, unlike a double-click which has
        # to land on the Guide cell itself -- otherwise it's unreachable without a mouse.
        self.skill_table.rowActivated.connect(
            lambda row, _column: self._open_skill_guide(row, _SKILL_GUIDE_COLUMN)
        )
        self._install_row_menu(
            self.skill_table,
            self._build_guide_row_menu(
                self.skill_table, _SKILL_GUIDE_COLUMN, self._open_skill_guide
            ),
        )
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
            [
                "Activity",
                "Status",
                "Missing skills",
                "Missing gear",
                "Est. GP/hr",
                "Notes",
                "Guide",
            ],
            minimum_widths={0: 200, 2: 220, 3: 260, 5: 260, 6: 110},
            maximum_widths={5: 380},
            text_columns={1, 2, 3, 5, 6},
        )
        self.pvm_table.cellDoubleClicked.connect(self._open_pvm_guide)
        self.pvm_table.rowActivated.connect(
            lambda row, _column: self._open_pvm_guide(row, _PVM_GUIDE_COLUMN)
        )
        self._install_row_menu(
            self.pvm_table,
            self._build_guide_row_menu(self.pvm_table, _PVM_GUIDE_COLUMN, self._open_pvm_guide),
        )
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
        multi_select: bool = False,
    ) -> QTableWidget:
        """A table sized from its content, within per-column bounds.

        ``maximum_widths`` overrides ``DEFAULT_MAXIMUM_COLUMN_WIDTH``; a column's minimum
        always wins over its maximum. Columns are right-aligned by default (for figures);
        ``text_columns`` marks the ones holding prose instead, which are left-aligned.

        ``multi_select`` allows the usual ctrl/shift range selection, for a table whose row
        actions can act on a batch. Off elsewhere, since a table whose actions only ever
        apply to one row should not let somebody select five and wonder why.
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
        # Qt defaults a sorting-enabled table to an active column-0 sort indicator, which
        # would silently re-sort every _fill_table() population. Clear it so tables keep
        # their intended order until a user clicks a header.
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        table.setProperty("minimumColumnWidths", minimum_widths or {})
        table.setProperty("maximumColumnWidths", maximum_widths or {})
        table.setProperty("textColumns", text_columns or set())
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if multi_select
            else QAbstractItemView.SelectionMode.SingleSelection
        )
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
        client = configured_web_client()
        dialog = SettingsDialog(
            self._theme,
            self._journal.database_path,
            client.base_url,
            client.token,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._theme = dialog.theme.currentText()
        QSettings().setValue("appearance/theme", self._theme)
        self._apply_theme(self._theme)
        self._save_website_settings(
            dialog.web_base_url_field.text().strip(),
            dialog.web_token_field.text().strip(),
        )
        if dialog.requested_database_path is not None:
            self._change_database_location(dialog.requested_database_path)

    def _save_website_settings(self, base_url: str, token: str) -> None:
        """Persist where this app reads from, and start reading from there.

        Rebuilds the importer so a newly pasted token takes effect immediately rather than
        needing a restart.
        """
        if (base_url, token) == (
            str(QSettings().value(WEB_BASE_URL_KEY, DEFAULT_BASE_URL) or ""),
            str(QSettings().value(WEB_TOKEN_KEY, "") or ""),
        ):
            return
        QSettings().setValue(WEB_BASE_URL_KEY, base_url)
        QSettings().setValue(WEB_TOKEN_KEY, token)
        self._sync_importer = build_sync_importer()
        # A new credential may be a different account, so the mirror starts fresh rather
        # than carrying over watermarks from the old one.
        self._journal_mirror = JournalMirror(self._journal, configured_web_client())
        self._last_sync_message = ""
        self._last_mirror_message = ""

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
            QMessageBox.warning(self, "Could not change database location", f"{exc}")
            return
        self._journal = new_repository
        QSettings().setValue("journal/database_path", str(new_path))
        self._render_journal()
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
        self.account_button.setText(
            "Connect character" if self._profile is None else "Change character"
        )
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
        """Replace the shown recommendation with the next-best combination, excluding
        any item already recommended this session."""
        if not self._points:
            return
        remaining = [
            candidate
            for candidate in self._flips
            if candidate.item_id not in self._excluded_item_ids
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
        values = [
            [
                item.name,
                _gp(item.buy_price),
                _gp(item.sell_price),
                f"{item.suggested_quantity:,}",
                _gp(item.profit_each),
                f"{item.roi:.2f}%",
                f"{item.hourly_volume:,}",
                f"{item.buy_limit:,}" if item.buy_limit else "—",
                _gp(item.potential_profit),
                f"{item.confidence}%",
            ]
            for item in rows
        ]
        floor = int(STRATEGIES[self.strategy.currentText()]["min_confidence"])

        def decorate_flip(row_index: int) -> None:
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
            offer_count = len(self._portfolio)
            self.track_top_button.setText(
                f"Track all {offer_count} recommended offer{'' if offer_count == 1 else 's'}"
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
        # Every column but the item name is fixed width, so the item name takes all the
        # slack and the figures stay in one block on the right.
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
            key=lambda saved_id: (
                self._mappings.get(saved_id, ItemMapping(saved_id, "", False, None, None)).name
            ),
        ):
            item, point = self._mappings.get(item_id), point_by_id.get(item_id)
            if item is None or point is None:
                continue
            buy_price, sell_price = offer_targets(point)
            profit = sell_price - buy_price - ge_tax(sell_price)
            roi = profit / buy_price * 100 if buy_price else 0
            age = max(0, now - min(point.high_time, point.low_time))
            rows.append(
                [
                    item.name,
                    _gp(buy_price),
                    _gp(sell_price),
                    _gp(profit),
                    f"{roi:.2f}%",
                    f"{point.volume_1h:,}",
                    f"{age // 60} min",
                ]
            )
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
        # Every journal mutation funnels through here, so this is where a status change
        # (a buy finishing, a sale closing the flip out) gets noticed.
        statuses = {trade.position_id: trade.status for trade in tracked}
        self._flash_journal_rows(journal_alert_positions(self._journal_statuses, statuses))
        self._journal_statuses = statuses
        self._journal_item_ids = {trade.position_id: trade.item_id for trade in tracked}
        selected_filter = self.journal_status_filter.currentText()
        selected_period = self.journal_period_filter.currentText()
        # Local, not UTC, matching _refresh_overnight_suggestions' day boundary.
        now = datetime.now().astimezone()
        point_by_id = {point.item_id: point for point in self._points}
        # Read once for the whole table rather than per row: this is a file read.
        slots = self._placed_offers()
        placed_item_ids = self._items_with_a_live_buy_offer(slots)
        if self._adopt_live_asks(tracked, self._live_sell_asks(slots)):
            tracked = self._journal.list_tracked()
        # Over every tracked position, not just the rendered ones -- a hidden row doesn't
        # stop the Grand Exchange working on that item.
        self._live_offers = self._ge_offers_in_flight(slots, self._offer_screen)
        self._live_price_sides = live_price_highlights(
            self._live_offers,
            [(trade.position_id, trade.item_id, trade.status) for trade in tracked],
        )

        def _live_sell_price(trade: TrackedTrade) -> int | None:
            point = point_by_id.get(trade.item_id) if trade.item_id is not None else None
            if point is None:
                return None
            try:
                _live_buy_price, live_sell_price = offer_targets(point)
            except ValueError:
                return None
            return live_sell_price

        # Computed over every tracked position, not scoped to the status/period filters --
        # this is a "needs a look" signal, not a filtered statistic. The explanation for
        # each flag is built here too, since the table has no column for the live
        # suggestion or the real ask.
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
        # Position id -> (what to actually ask, whether that clears what was paid).
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
            # A "Bought" row shows ``_ask_price``'s number here, not the plan's frozen target.
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
            # Only a tracked position carries a flash key -- a manual entry's trade_id and
            # a position_id can collide, since they're numbered from different tables.
            if row.raw_status in UpdateTrackedTradeDialog.STATUSES:
                self.journal_table.item(row_index, 0).setData(_FLASH_KEY_ROLE, row.record_id)
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
                # Stays picked out in amber until listed, since the flash itself is over
                # in two seconds while the player is still in-game.
                ask_cell = self.journal_table.item(row_index, 6)
                ask_font = ask_cell.font()
                ask_font.setBold(True)
                ask_cell.setFont(ask_font)
                _ask_price_value, ask_is_sound = ask_detail.get(row.record_id, (None, True))
                if ask_is_sound:
                    ask_cell.setForeground(QColor(self._warning_color))
                    ask_cell.setToolTip("Ready to list.\nThis is the price to ask for it.")
                else:
                    # Neither the live market nor the original plan clears the buy price.
                    ask_cell.setForeground(QColor(self._loss_color))
                    ask_cell.setToolTip(
                        "Ready to list, but neither the market nor the original plan "
                        "clears what this cost — listing here would sell at a loss "
                        "after tax. Use your own judgement on the price."
                    )

            attention = attention_detail.get(row.record_id) if row.needs_attention else None
            if row.needs_attention:
                self.journal_table.item(row_index, 2).setForeground(QColor(self._warning_color))
            if attention is not None:
                # Every cell, not just the warning one -- the flag is about the row.
                for column in range(self.journal_table.columnCount()):
                    self.journal_table.item(row_index, column).setToolTip(attention)

            # Runs last so its tooltip joins rather than replaces what the blocks above set.
            if row.raw_status in UpdateTrackedTradeDialog.STATUSES:
                self._mark_live_prices(
                    row_index, row, self._live_price_sides.get(row.record_id, frozenset())
                )

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
        # Decorated through _fill_table's decorate hook, not after: the Status cell's sort
        # key needs to be set before the table sorts by that column.
        self._fill_table(
            self.journal_table,
            [row.cells for row in rendered_rows],
            green_columns=set(),
            row_ids=[row.record_id for row in rendered_rows],
            decorate=decorate_row,
        )
        # Fresh cells have no background, so a blink part-way through has to be reapplied.
        self._paint_journal_flash()

        # Same summarize/realized_results helpers the Performance page uses, so the two
        # pages' figures for the same period always agree.
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
        # From the dict, not the set: list_tracked is newest first and dicts keep that.
        self._attention_positions = list(attention_detail)
        self.journal_attention.set_live(bool(attention_positions))
        self.journal_attention.setText(f"Needs attention\n{len(attention_positions):,}")
        # Negated: any positive count should read as the card's "negative" (warning) tone;
        # zero should read neutral. There is no profit/loss sign to reuse here directly.
        self._set_money_state(self.journal_attention, -len(attention_positions))
        self._render_supplies_spend(tracked)
        self._render_performance()
        # A filter change re-renders without calling _flash_journal_rows, so a flash
        # queued behind the old filter needs this to get its chance to play.
        self._release_pending_flashes()
        # Rebuilding the table may have dropped the selection; Qt only announces that when
        # rows actually go, so ask directly.
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

        Empty before the first market load completes; ``apply_offer_opened`` and
        ``apply_synced_ge_fill`` treat that the same as no suggestion at all.
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

    def _mirror_journal(self) -> None:
        """One exchange with the website, on a timer.

        Runs on this thread deliberately -- a worker thread would need a second journal
        connection and a merge racing the window's own writes, to avoid a pause nobody sees.
        """
        if not self._journal_mirror.client.configured:
            return
        try:
            result = self._journal_mirror.sync()
        except Exception as exc:  # noqa: BLE001 - a sync failure must not break the UI loop.
            self._last_mirror_message = f" • journal sync paused: {exc}"
            return
        self._last_mirror_message = "" if result.checked_only else f" • {result.describe()}"
        # Redraw the status line now rather than waiting up to three seconds for its own timer.
        self._update_runelite_status()
        if result.changed:
            self._refresh_journal_views()

    def _refresh_journal_views(self) -> None:
        """Redraw what reads the journal, after a sync changed it underneath.

        Only these two -- the mirror carries trades and tracked positions and nothing else,
        so GE panels, buy limits, and the loadout are unaffected.
        """
        self._render_journal()
        self._render_performance()

    def _apply_mirror_interval(self, active: bool) -> None:
        """Ask less often while nothing on screen is waiting for the answer."""
        self._mirror_timer.setInterval(
            MIRROR_INTERVAL_MS if active else MIRROR_BACKGROUND_INTERVAL_MS
        )

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
            self._render_loot_log()
            self._render_death_log()
        if result.imported:
            self._loadout_snapshot = self._journal.get_latest_loadout_snapshot()
            self._render_pvm()
            if result.applied_to_tracked:
                self._render_journal()
        # Recomputed every tick, not just on import: purchases age out of the rolling
        # 4-hour buy-limit window purely by the clock moving forward.
        self._render_buy_limits()
        # Also unconditional: the plugin's offer-state file can change without a sync event.
        self._render_ge_offers()
        self._refresh_live_offers()

    def _render_buy_limits(self) -> None:
        limits = {
            item.item_id: item.buy_limit
            for item in self._mappings.values()
            if item.buy_limit is not None
        }
        now = datetime.now(UTC)
        # Bounded to the 4-hour window buy_limit_status actually uses, since this is called
        # every 3 seconds and loading full history would get slower over time.
        statuses = buy_limit_status(
            self._journal.list_synced_trades("ge_fill", since=now - BUY_LIMIT_WINDOW),
            limits,
            now,
        )
        self.buy_limits_empty.setVisible(not statuses)
        # An empty table means something different with the plugin connected vs. not.
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
        """The account's Grand Exchange slots, or None if there's nothing to read them from.

        Read once per render and asked several questions, because it is a file read.
        """
        account_hash = self._synced_account_hash()
        if account_hash is None:
            return None
        return self._sync_importer.read_placed_offers(account_hash)

    @staticmethod
    def _items_with_a_live_buy_offer(
        slots: dict[int, GEOfferSlot] | None,
    ) -> frozenset[int] | None:
        """Items one of the eight Grand Exchange slots is currently holding a buy for.

        None means there's nothing to judge against, distinct from an empty set (no buys
        placed), so the Journal can tell "not placed" apart from "cannot say". A slot
        counts until the player collects it, even once its offer has finished.
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

        Backfills positions listed before ``apply_offer_opened`` existed to write this
        directly. Writes only where it disagrees, so this costs nothing once they agree.
        Returns whether anything changed, since the caller is holding stale positions.
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

    def _synced_account_hash(self) -> str | None:
        """Whose Grand Exchange state to draw, held steady through a moment offline.

        ``connection_status`` reports no character at all when the source did not answer,
        which is a different thing from there being no character to report. Since the desktop
        reads through the website rather than off disk, "did not answer" is now an ordinary
        event -- one slow request over a home connection -- and ``WebAppSource`` caches the
        empty answer for a couple of seconds on top, so every consumer in that window agrees
        there is nobody. Taken literally, that emptied the Grand Exchange panel mid-session,
        over and over, while the plugin was sending perfectly the whole time.

        So a blip keeps the last character, and only an answer that genuinely names nobody --
        the token cleared, or a pairing nobody has played -- puts the panel back to empty.
        """
        connection = self._sync_importer.connection_status()
        if connection.account_hash is not None:
            self._last_synced_account_hash = connection.account_hash
        elif connection.source_reachable:
            self._last_synced_account_hash = None
        return self._last_synced_account_hash

    def _render_ge_offers(self) -> None:
        account_hash = self._synced_account_hash()
        self.ge_offers_empty.setVisible(account_hash is None)
        self.ge_slots_frame.setVisible(account_hash is not None)
        if account_hash is None:
            # Reset rather than kept: a newly connected character's slots aren't "new" events.
            self._ge_slot_states = None
            self._ge_terminal_items = None
            return
        slots = self._sync_importer.read_offer_state(account_hash)
        states = {slot_index: slot.state for slot_index, slot in slots.items()}
        # Filled, not merely finished: a cancelled offer is uncollected too, but the player
        # cancelled it a second ago and does not need calling back to look at it.
        self._flash_ge_slots(newly_reached(self._ge_slot_states, states, FILLED_OFFER_STATES))
        self._ge_slot_states = states
        terminal_items = frozenset(
            slot.item_id for slot in slots.values() if slot.is_terminal and slot.item_id > 0
        )
        if self._ge_terminal_items is not None:
            self._cancel_stale_completed_flashes(self._ge_terminal_items - terminal_items)
        self._ge_terminal_items = terminal_items
        for slot_index, card in enumerate(self.ge_slot_cards):
            slot = slots.get(slot_index)
            if slot is None:
                card.show_empty()
            else:
                card.show_offer(slot)
        # show_empty/show_offer both reset the card's look, so the beat currently showing
        # has to be put back on top of it.
        self._paint_slot_flash()

    @staticmethod
    def _ge_offers_in_flight(
        slots: dict[int, GEOfferSlot] | None, screen: GEOfferScreen | None
    ) -> frozenset[tuple[int, str | None]]:
        """Every buy or sale the Grand Exchange has going, as (item id, side).

        One entry per occupied slot -- which is the trade itself, and lasts from confirming
        the offer until collecting it, whether or not the player is standing at the GE --
        plus the "Set up offer" box while one is open, which is the moment before the offer
        exists and the one where the price is actually being typed.

        A union, not a choice between the two. Opening a box on one item does not stop the
        other seven slots filling, and a highlight that jumped to whichever screen was up
        was following the player around rather than following the trade.
        """
        offers = {(slot.item_id, slot.side) for slot in (slots or {}).values() if slot.item_id > 0}
        if screen is not None and screen.focused:
            offers.add((screen.item_id, screen.side))
        return frozenset(offers)

    def _refresh_live_offers(self) -> None:
        """Re-read what the Grand Exchange is working on, and redraw the journal if it moved.

        Redraws only when the answer actually changes -- re-rendering every 3-second tick
        would fight the table's selection and scroll position while the player is reading it.
        Polled rather than event-driven because collecting an offer empties a slot without
        producing any event the journal would otherwise hear about.
        """
        account_hash = self._synced_account_hash()
        screen = (
            None if account_hash is None else self._sync_importer.read_offer_screen(account_hash)
        )
        offers = self._ge_offers_in_flight(self._placed_offers(), screen)
        if (screen, offers) == (self._offer_screen, self._live_offers):
            return
        self._offer_screen = screen
        self._live_offers = offers
        self._render_journal()

    def _flash_journal_rows(self, position_ids: Iterable[int]) -> None:
        """Queue an attention blink on these journal rows."""
        pending = set(position_ids)
        if not pending:
            return
        self._pending_journal_flash |= pending
        self._release_pending_flashes()

    def _cancel_stale_completed_flashes(self, collected_item_ids: frozenset[int]) -> None:
        """Drop a queued "come see this" flash once collecting it already answered it.

        A "Completed" flash means "the coins are waiting for you on the GE" -- if they're
        collected (a slot disappearing) before the flash ever plays, painting it afterward
        would only announce money the player already has. A "Bought" flash means something
        else (go list this) and isn't affected by collection, so it stays queued.
        """
        if self._journal_statuses is None or not collected_item_ids:
            return
        stale = {
            position_id
            for position_id in self._pending_journal_flash
            if self._journal_item_ids.get(position_id) in collected_item_ids
            and self._journal_statuses.get(position_id) == FLIP_CLOSED_STATUS
        }
        if stale:
            self._pending_journal_flash -= stale
            self._update_journal_badge()

    def _flash_ge_slots(self, slot_indexes: Iterable[int]) -> None:
        """Queue an attention blink on these Grand Exchange slot cards."""
        pending = set(slot_indexes)
        if not pending:
            return
        self._pending_slot_flash |= pending
        self._release_pending_flashes()

    def _release_pending_flashes(self) -> None:
        """Play whatever is queued once its surface is on screen and being looked at.

        A blink queued while the player is off in RuneLite would otherwise be spent on
        nobody -- it waits for the window to be focused/unminimised and the right
        page/tab showing, with a sidebar dot marking that something is waiting.

        A journal row also waits on the Status filter: a sale finishing is exactly the
        transition that can drop a row out of the default "Active trades" filter, so a
        flash for a row the current filter won't render stays queued until a wider
        filter (or the next re-render) brings the row into view.
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

        The blink itself only plays when someone is looking; this stays put until they
        arrive, for a player who was in-game when it happened.
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
            '"All statuses" to find it.'
            if waiting
            else f"{title}  (Ctrl+{self._journal_page_index() + 1})"
        )
        self.journal_tabs.setTabText(
            _PLANS_TAB_INDEX,
            _PLANS_TAB_TITLE + ("  ●" if self._pending_journal_flash else ""),
        )

    # Journal columns holding the price each side of a trade is working towards.
    _PRICE_COLUMN: ClassVar[dict[str, int]] = {"buy": 4, "sell": 6}

    def _mark_live_prices(self, row_index: int, row: _JournalRow, sides: frozenset[str]) -> None:
        """Pick out the price a live Grand Exchange offer is working on, and only that.

        The whole point of the mark is "this is the number to type", so it goes on the one
        cell holding that number and nothing else -- no row wash, no second figure. It holds
        for the whole buy or sale, from opening the offer box to collecting the slot.

        A price cell already carrying the "ready to list" colour keeps it: that colour says
        something (whether the price clears what was paid) this mark can't.
        """
        for side in sorted(sides):
            column = self._PRICE_COLUMN[side]
            cell = self.journal_table.item(row_index, column)
            if cell is None:
                continue
            cell_font = cell.font()
            cell_font.setBold(True)
            cell.setFont(cell_font)
            already_coloured = column == 6 and row.raw_status == READY_TO_SELL_STATUS
            if not already_coloured:
                cell.setForeground(QColor(self._live_offer_color))
            hint = (
                "The Grand Exchange is buying this item right now — this is the price the "
                "row is planned to buy at."
                if side == "buy"
                else "The Grand Exchange is selling this item right now — this is the price "
                "the row is asking."
            )
            existing = cell.toolTip()
            cell.setToolTip("\n\n".join(part for part in (hint, existing) if part))

    def _paint_journal_flash(self) -> None:
        """Wash the blinking rows, and clear the wash the beat it stops applying."""
        table = self.journal_table
        lit_brush = QBrush(QColor(self._flash_row_color))
        for row in range(table.rowCount()):
            anchor = table.item(row, 0)
            if anchor is None:
                continue
            # Null brush, not the table's background colour, hands the row back to the
            # alternating-row colours instead of freezing it on one.
            lit = self._journal_flasher.is_lit(anchor.data(_FLASH_KEY_ROLE))
            brush = lit_brush if lit else QBrush()
            for column in range(table.columnCount()):
                cell = table.item(row, column)
                # Only where it actually changes -- setting a cell to a brush it already
                # has still repaints it, and this runs six times per blink.
                if cell is not None and cell.background() != brush:
                    cell.setBackground(brush)

    def _paint_slot_flash(self) -> None:
        for slot_index, card in enumerate(self.ge_slot_cards):
            card.set_flashing(self._slot_flasher.is_lit(slot_index))

    def _reveal_offer_in_journal(self, item_id: int) -> None:
        """Answer "which row is this offer?" from the slot card's side.

        If the current filters are hiding the row, they're widened until it shows.
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
            # A completed flip can fall outside the period filter as easily as the status
            # one, so both give way; each setter re-renders the table on its way through.
            self.journal_status_filter.setCurrentText(JOURNAL_STATUS_FILTERS[0])
            self.journal_period_filter.setCurrentText(PERIOD_FILTERS[0])
            if not self._select_journal_position(position_id):
                return
        # focus, not start: this replaces whatever was blinking rather than joining it.
        self._journal_flasher.focus({position_id})

    def _reveal_attention_positions(self) -> None:
        """Answer the Needs attention card: show me the rows you are counting.

        The newest is selected and scrolled to, but the whole set blinks -- pointing at
        just one would only answer half the count.
        """
        if not self._attention_positions:
            return
        newest = self._attention_positions[0]
        self.journal_tabs.setCurrentIndex(_PLANS_TAB_INDEX)
        if not self._select_journal_position(newest):
            self.journal_status_filter.setCurrentText(JOURNAL_STATUS_FILTERS[0])
            self.journal_period_filter.setCurrentText(PERIOD_FILTERS[0])
            # Each setter re-renders on its way through, which rebuilds the list.
            if not self._attention_positions:
                return
            newest = self._attention_positions[0]
            if not self._select_journal_position(newest):
                return
        self._journal_flasher.focus(self._attention_positions)

    def _journal_position_for_item(self, item_id: int) -> int | None:
        """The journal row a Grand Exchange slot is about.

        The newest in-progress position for the item, falling back to the newest of any
        status so an uncollected finished flip is still found. ``list_tracked`` returns
        newest first.
        """
        matches = [trade for trade in self._journal.list_tracked() if trade.item_id == item_id]
        chosen = next(
            (trade for trade in matches if journal_status_matches(trade.status, "Active trades")),
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
        elif connection.detected and not connection.source_reachable:
            # Not the plugin's fault -- it's the website (where we read from) unreachable.
            button_text = "Website unreachable"
            reason = f" ({connection.last_error})" if connection.last_error else ""
            status_text = (
                f"Cannot reach the website{reason} • your journal is safe on this PC and "
                "will catch up on its own"
            )
        elif connection.detected:
            button_text = "RuneLite offline"
            status_text = "RuneLite offline • saved activity will import when available"
        else:
            button_text = "Connect RuneLite"
            status_text = "Connect RuneLite to import GE fills and optional player trades"
        self.runelite_button.setText(button_text)
        # Dropped only when it would just repeat the "website unreachable" text above.
        mirror_message = self._last_mirror_message
        if (
            connection.detected
            and not connection.source_reachable
            and mirror_message.endswith(MirrorResult(reached=False).describe())
        ):
            mirror_message = ""
        self.runelite_status.setText(status_text + self._last_sync_message + mirror_message)
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

    def _render_loot_log(self) -> None:
        events = self._journal.list_npc_loot_events()
        values = [
            [
                _display_timestamp(event.occurred_at),
                event.npc_name,
                event.account_name,
                _compact_items(event.items),
                _gp(event.total_value),
            ]
            for event in events
        ]
        self._fill_table(
            self.loot_log_table,
            values,
            green_columns=set(),
            row_ids=[event.event_id for event in events],
        )
        self._loot_log_selection_changed()

    def _selected_loot_log_event_id(self) -> str | None:
        row = self.loot_log_table.currentRow()
        if row < 0 or row >= self.loot_log_table.rowCount():
            return None
        event_id = self.loot_log_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return event_id if isinstance(event_id, str) else None

    def _delete_selected_loot_event(self) -> None:
        event_id = self._selected_loot_log_event_id()
        if event_id is None:
            QMessageBox.information(self, "No entry selected", "Select a loot log row first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete loot entry",
            "Remove this loot delivery from the journal? This does not affect RuneLite or the game.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._journal.delete_npc_loot_event(event_id)
            self._render_loot_log()

    def _render_death_log(self) -> None:
        events = self._journal.list_player_death_events()
        values = [
            [
                _display_timestamp(event.occurred_at),
                event.account_name,
                "Skulled" if event.skulled else "Not skulled",
                _compact_items((*event.equipment, *event.inventory)),
                _gp(event.total_value),
            ]
            for event in events
        ]
        self._fill_table(
            self.death_log_table,
            values,
            green_columns=set(),
            row_ids=[event.event_id for event in events],
        )
        self._death_log_selection_changed()

    def _selected_death_log_event_id(self) -> str | None:
        row = self.death_log_table.currentRow()
        if row < 0 or row >= self.death_log_table.rowCount():
            return None
        event_id = self.death_log_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return event_id if isinstance(event_id, str) else None

    def _delete_selected_death_event(self) -> None:
        event_id = self._selected_death_log_event_id()
        if event_id is None:
            QMessageBox.information(self, "No entry selected", "Select a death log row first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete death entry",
            "Remove this death from the journal? This does not affect RuneLite or the game.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._journal.delete_player_death_event(event_id)
            self._render_death_log()

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
        # Reuses the "just finished" blink to say "these are the ones you asked for".
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
        rows = self._selected_journal_rows()
        if not rows:
            QMessageBox.information(self, "No trade selected", "Select a journal row first.")
            return
        # Read every row's identity before deleting any of them: each delete re-renders the
        # table, so row numbers collected up front stop meaning what they meant.
        doomed = [
            (
                self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole),
                self.journal_table.item(row, 1).data(Qt.ItemDataRole.UserRole),
            )
            for row in rows
        ]
        doomed = [(record_id, status) for record_id, status in doomed if isinstance(record_id, int)]
        if not doomed:
            return
        question = (
            "Remove this trade from your journal?"
            if len(doomed) == 1
            else f"Remove these {len(doomed)} trades from your journal?"
        )
        answer = QMessageBox.question(
            self,
            "Delete trade" if len(doomed) == 1 else "Delete trades",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for record_id, status in doomed:
            if status in UpdateTrackedTradeDialog.STATUSES:
                self._journal.delete_tracked(record_id)
            else:
                self._journal.delete(record_id)
        self._render_journal()
        self._render_performance()

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
            # utf-8-sig so Excel reads non-ASCII text correctly; newline="" avoids
            # doubling up the csv module's own line endings.
            Path(path).write_text(content, encoding="utf-8-sig", newline="")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", f"Could not write the file: {exc}")
            return
        QMessageBox.information(self, "Export complete", f"Journal exported to {path}")

    def _import_journal_csv(self) -> None:
        """Read a journal CSV back in, adding to this journal or replacing it.

        Nothing is written before the confirmation, and the confirmation names the number of
        trades replacing would delete -- the file is chosen in one dialog and the destructive
        option lives in the next, so "replace" is never a thing that happens because somebody
        clicked through a file picker on autopilot.
        """
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "Import Trade Journal", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, "Import failed", f"Could not read the file: {exc}")
            return
        try:
            parsed = parse_journal_csv(content)
        except CsvImportError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        if not parsed.trades:
            detail = "\n".join(parsed.skipped[:10]) or "The file has no rows."
            QMessageBox.warning(
                self,
                "Nothing to import",
                "No completed trades were found in that file, so there is nothing to "
                f"import.\n\n{detail}",
            )
            return

        existing = len(self._journal.list_all())
        box = QMessageBox(self)
        box.setWindowTitle("Import Trade Journal")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Import {len(parsed.trades)} trade(s) from this file?")
        box.setInformativeText(summarise(parsed, replacing=False, existing=existing))
        if parsed.skipped:
            box.setDetailedText("\n".join(parsed.skipped))
        add = box.addButton("Add to journal", QMessageBox.ButtonRole.AcceptRole)
        replace = box.addButton("Replace journal…", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(add)
        box.exec()
        chosen = box.clickedButton()
        if chosen is None or chosen not in (add, replace):
            return

        if chosen is replace:
            # Asked twice on purpose. The first dialog is about a file; this one is about
            # deleting trades that are already here, which is the part with no undo.
            confirm = QMessageBox.warning(
                self,
                "Replace the journal?",
                summarise(parsed, replacing=True, existing=existing),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            for record in self._journal.list_all():
                self._journal.delete(record.trade_id)

        for trade in parsed.trades:
            self._journal.add(trade.item_name, trade.quantity, trade.buy_price, trade.sell_price)
        self._render_journal()
        self._render_performance()
        skipped_note = f"\n\n{len(parsed.skipped)} row(s) were skipped." if parsed.skipped else ""
        QMessageBox.information(
            self,
            "Import complete",
            f"Imported {len(parsed.trades)} trade(s).{skipped_note}",
        )

    def _selected_journal_rows(self) -> list[int]:
        """The selected rows, top to bottom, however they were selected.

        ``selectedIndexes`` returns one index per cell, so a row selection arrives nine times
        over; the set collapses that back to rows.
        """
        rows = {index.row() for index in self.journal_table.selectedIndexes()}
        return sorted(rows)

    def _selected_tracked_positions(self, rows: list[int]) -> tuple[list[int], int]:
        """(position ids that can be refiled, how many selected rows could not be).

        A manually completed entry is not a tracked position and has no status to change, so
        it is counted rather than silently treated as one.
        """
        position_ids: list[int] = []
        ineligible = 0
        for row in rows:
            record_id = self.journal_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            status = self.journal_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
            if isinstance(record_id, int) and status in UpdateTrackedTradeDialog.STATUSES:
                position_ids.append(record_id)
            else:
                ineligible += 1
        return position_ids, ineligible

    def _retag_selected_trades(self, rows: list[int]) -> None:
        """Refile several positions at once, for the shopping-trip case."""
        position_ids, ineligible = self._selected_tracked_positions(rows)
        if not position_ids:
            QMessageBox.information(
                self,
                "Nothing to update",
                "None of the selected rows is a tracked position. Manually completed entries "
                "cannot change status.",
            )
            return
        characters = [
            (account.get("account_hash"), account.get("account_name") or "Unnamed character")
            for account in self._sync_importer.known_accounts()
            if account.get("account_hash")
        ]
        characters.append((None, "No character in particular"))
        dialog = RetagTradesDialog(len(position_ids), list(STRATEGIES), characters, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self._journal.retag_positions(
                position_ids,
                status=dialog.chosen_status(),
                strategy=dialog.chosen_strategy(),
                account_hash=dialog.chosen_character(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not update trades", str(exc))
            return
        self._render_journal()
        self._render_performance()
        notes = []
        if result.skipped:
            notes.append(
                f"{len(result.skipped)} left as they were: their fills do not cover the full "
                "quantity, so they cannot be Completed."
            )
        if ineligible:
            notes.append(f"{ineligible} selected row(s) were not tracked positions.")
        QMessageBox.information(
            self,
            "Trades updated",
            f"Updated {result.updated} trade(s)." + ("\n\n" + "\n".join(notes) if notes else ""),
        )

    def _update_selected_trade(self) -> None:
        rows = self._selected_journal_rows()
        if len(rows) > 1:
            self._retag_selected_trades(rows)
            return
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

        self._fill_table(self.skill_table, values, green_columns={5, 7}, decorate=decorate_row)
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
        if isinstance(guide_url, str) and guide_url.startswith("https://oldschool.runescape.wiki/"):
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
            estimate_gp_per_hour(result.activity, self._mappings, self._points)
            for result in results
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
            guide_cell.setToolTip(
                f"{result.activity.notes}\nDouble-click to open the OSRS Wiki page."
            )
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

        ``decorate`` must run while sorting is still suspended (it does, here), so its row
        index reliably means "the nth entry the caller passed in".
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
                # resizeColumnsToContents() measures text alone; add back the stylesheet's
                # cell padding or a figure sized to the pixel elides anyway.
                width = max(table.columnWidth(column), minimum) + CELL_PADDING_WIDTH
                # Cap a free-text column so its longest row can't push everything after it
                # off the right edge; the full text stays available in the tooltip.
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

        Takes ``table`` as an argument rather than using ``sender()``, since this is
        reached four ways (double-click, Enter, row menu, watchlist page) and only two
        of those have a sender to ask.
        """
        item_id = self._row_item_id(table, row)
        if item_id is None:
            return
        mapping = self._mappings.get(item_id)
        point = next(
            (candidate for candidate in self._points if candidate.item_id == item_id), None
        )
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
        self._live_offer_color = palette.live_offer
        self._update_journal_badge()
        # The game draws interfaces with hard pixel edges, so the Old School theme squares
        # off the corners the modern themes round.
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
            /* Descendants only: these two are frames holding labels, which would otherwise
               repaint the window colour as a block behind the text. */
            #recommendation QLabel, #brandCard QLabel {{ background: transparent; }}
            #summaryCard {{ background: {palette.field}; color: {palette.text}; border: 1px solid {palette.border}; border-radius: {panel}; padding: 14px; font-size: 14px; }}
            #summaryCard[moneyState="positive"] {{ color: {palette.profit}; }}
            #summaryCard[moneyState="negative"] {{ color: {palette.loss}; }}
            #geSlot {{ background: {palette.field}; border: 1px solid {palette.border}; border-radius: {panel}; min-height: 86px; }}
            #geSlot[slotState="empty"] {{ background: transparent; border: 1px dashed {palette.border}; }}
            #geSlot[slotState="buy"] {{ border-color: {palette.link}; }}
            #geSlot[slotState="sell"] {{ border-color: {palette.bought}; }}
            #geSlot[slotState="collect"] {{ border-color: {palette.profit}; }}
            /* Let the card's colour through instead of repainting the window background. */
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
            /* Attention blink, last so it wins ties with the slotState rules above by
               source order. Reverts automatically when the property clears. */
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
            /* Unstyled, tooltips use the OS's own colours, which read poorly on a dark window. */
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
        # Recorded before the window opens so a failure here can't make it reappear every launch.
        QSettings().setValue(LAST_SEEN_VERSION_KEY, __version__)
        # A first run announces only the current version, not the whole release history.
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
        # Fails quietly at startup (usually just no network); Settings offers a visible retry.
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
        # __init__ resizes before building the UI, so the first call can arrive with no
        # card to measure yet.
        rows = getattr(self, "flip_recommendation_rows", None)
        if rows is not None:
            rows.fit()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        # Coming back to the window is when a flash held while the player was in-game
        # finally has an audience.
        if event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        ):
            self._release_pending_flashes()
            # Also restore the quick poll cadence and check once immediately, rather than
            # leaving a stale table for up to a minute.
            active = self.isActiveWindow()
            self._apply_mirror_interval(active)
            if active:
                self._mirror_journal()

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

    ``display_status`` is what the Status cell shows; ``raw_status`` is what's stored and
    what buttons act on. They differ when a plan with no offer placed reads "Planned".
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


def _ask_price(trade: TrackedTrade, live_sell_price: int | None) -> tuple[int, bool]:
    """What a "Bought" row should tell the player to ask, and whether that number is sound.

    ``trade.sell_suggestion`` is frozen at planning time, which can be hours stale for a
    Quick/Balanced strategy that never revisits it; the live sell target wins whenever
    there is one, falling back to the frozen suggestion otherwise.

    "Sound" means the number clears what was actually paid, after tax -- a stale plan or
    an auto-created position (see ``TrackedTrade.asking_price``) can otherwise suggest a
    guaranteed loss.
    """
    price = live_sell_price if live_sell_price is not None else trade.sell_suggestion
    if trade.actual_buy is None:
        return price, True
    return price, price - ge_tax(price) > trade.actual_buy


def _leading_number(value: str) -> float:
    """Extract a formatted result's leading number for sign-aware coloring."""
    token = value.strip().removeprefix("Est. ").split(" ", 1)[0].replace(",", "")
    token = token.removesuffix("%")
    try:
        return float(token)
    except ValueError:
        return 0.0


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
    """Find bundled resources in releases and project resources during development.

    A compiled build marks every module with ``__compiled__`` and lays data files out
    beside the executable; a source checkout has them two directories above this file.
    """
    if "__compiled__" in globals():
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parents[2]
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
