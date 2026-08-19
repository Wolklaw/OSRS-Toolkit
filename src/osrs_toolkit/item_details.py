from __future__ import annotations

import time

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from osrs_toolkit.market import WikiMarketClient
from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint, TimeseriesPoint
from osrs_toolkit.ranking import ge_tax, offer_targets

_HISTORY_TIMESTEP = "6h"


class TimeseriesWorker(QObject):
    """Fetches one item's price history off the GUI thread.

    Owned by ``ItemDetailsDialog`` rather than defined alongside the app's other workers,
    since it exists only to serve that dialog's Price history tab.
    """

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, client: WikiMarketClient, item_id: int) -> None:
        super().__init__()
        self._client = client
        self._item_id = item_id

    def run(self) -> None:
        try:
            points = self._client.fetch_timeseries(self._item_id, _HISTORY_TIMESTEP)
            self.finished.emit(points)
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to the GUI.
            self.failed.emit(str(exc))


class ItemDetailsDialog(QDialog):
    def __init__(
        self,
        item: ItemMapping,
        point: MarketPoint,
        candidate: FlipCandidate | None,
        watched: bool,
        market_client: WikiMarketClient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.watched = watched
        self.track_requested = False
        self._item_id = item.item_id
        self._market_client = market_client
        self._history_thread: QThread | None = None
        self._history_worker: QObject | None = None
        self._history_requested = False
        self.setWindowTitle(item.name)
        self.setMinimumSize(570, 560)
        layout = QVBoxLayout(self)

        title = QLabel(item.name, objectName="title")
        layout.addWidget(title)
        descriptor = "Members item" if item.members else "Free-to-play item"
        layout.addWidget(QLabel(f"{descriptor} • Item ID {item.item_id}", objectName="muted"))

        tabs = QTabWidget()
        tabs.addTab(self._build_overview_tab(item, point, candidate), "Overview")
        history_tab, self._history_tab_index = self._build_history_tab(), 1
        tabs.addTab(history_tab, "Price history")
        tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(tabs, 1)

        self.watch_button = QPushButton()
        self.watch_button.clicked.connect(self._toggle_watchlist)
        self._update_watch_button()
        layout.addWidget(self.watch_button)
        if candidate is not None:
            track_button = QPushButton("Track this suggested trade")
            track_button.clicked.connect(self._request_tracking)
            layout.addWidget(track_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_overview_tab(
        self, item: ItemMapping, point: MarketPoint, candidate: FlipCandidate | None
    ) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        metrics = QGridLayout()
        buy, sell = offer_targets(point)
        tax = ge_tax(sell)
        profit = sell - buy - tax
        roi = profit / buy * 100 if buy else 0
        age = max(0, int(time.time()) - min(point.high_time, point.low_time))
        values = [
            ("Suggested buy offer", _gp(buy)),
            ("Suggested sell offer", _gp(sell)),
            ("Latest instant-sell trade", _gp(point.low)),
            ("Latest instant-buy trade", _gp(point.high)),
            ("5m average instant-sell", _gp(point.average_low_5m)),
            ("5m average instant-buy", _gp(point.average_high_5m)),
            ("GE tax per item", _gp(tax)),
            ("Observed net margin", _gp(profit)),
            ("Observed ROI", f"{roi:.2f}%"),
            ("One-hour volume", f"{point.volume_1h:,}"),
            ("Five-minute volume", f"{point.volume_5m:,}"),
            ("Oldest trade used", _duration(age)),
            ("Four-hour buy limit", f"{item.buy_limit:,}" if item.buy_limit else "Unknown"),
            ("High Alchemy value", _gp(item.high_alch) if item.high_alch else "Not available"),
            ("Five-minute midpoint", _number(point.average_5m)),
            ("One-hour midpoint", _number(point.average_1h)),
        ]
        for row, (label, value) in enumerate(values):
            metrics.addWidget(QLabel(label, objectName="muted"), row, 0)
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            metrics.addWidget(value_label, row, 1)
        tab_layout.addLayout(metrics)

        explanation = QLabel(_explanation(point, candidate), objectName="recommendation")
        explanation.setWordWrap(True)
        explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tab_layout.addWidget(explanation)
        tab_layout.addStretch()
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        self.history_status = QLabel("Loading price history…", objectName="status")
        self.history_status.setWordWrap(True)
        tab_layout.addWidget(self.history_status)

        self.history_chart = QChart()
        self.history_chart.legend().setVisible(True)
        self.history_chart.setBackgroundVisible(False)
        self.history_view = QChartView(self.history_chart)
        self.history_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.history_view.setMinimumHeight(320)
        self.history_view.hide()
        tab_layout.addWidget(self.history_view, 1)
        return tab

    def _tab_changed(self, index: int) -> None:
        # Fetched only once the tab is actually opened, both to avoid a network call for
        # the common case of a user who only checks the Overview metrics, and so a dialog
        # can be constructed in tests without triggering one.
        if index != self._history_tab_index or self._history_requested:
            return
        self._history_requested = True
        thread = QThread(self)
        worker = TimeseriesWorker(self._market_client, self._item_id)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._history_loaded)
        worker.failed.connect(self._history_failed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        # moveToThread does not parent the worker to anything, so without holding this
        # reference Python would garbage-collect it before the thread's event loop ever
        # gets to run it — the worker would simply never fire.
        self._history_thread = thread
        self._history_worker = worker
        thread.start()

    def _history_loaded(self, points: list[TimeseriesPoint]) -> None:
        usable = [p for p in points if p.average_high is not None or p.average_low is not None]
        if not usable:
            self.history_status.setText(
                "No price history is available for this item yet."
            )
            return
        buy_series = QLineSeries()
        buy_series.setName("Instant-buy average")
        sell_series = QLineSeries()
        sell_series.setName("Instant-sell average")
        prices: list[int] = []
        for entry in usable:
            timestamp_ms = entry.timestamp * 1000
            if entry.average_high is not None:
                buy_series.append(timestamp_ms, entry.average_high)
                prices.append(entry.average_high)
            if entry.average_low is not None:
                sell_series.append(timestamp_ms, entry.average_low)
                prices.append(entry.average_low)
        self.history_chart.addSeries(buy_series)
        self.history_chart.addSeries(sell_series)

        axis_x = QDateTimeAxis()
        axis_x.setFormat("MMM d")
        axis_x.setTitleText(f"Date ({_HISTORY_TIMESTEP} averages)")
        self.history_chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        buy_series.attachAxis(axis_x)
        sell_series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Price (gp)")
        axis_y.setLabelFormat("%.0f")
        axis_y.setRange(0, max(prices) * 1.05)
        self.history_chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        buy_series.attachAxis(axis_y)
        sell_series.attachAxis(axis_y)

        self.history_status.hide()
        self.history_view.show()

    def _history_failed(self, message: str) -> None:
        self.history_status.setText(f"Price history could not be loaded: {message}")

    def _toggle_watchlist(self) -> None:
        self.watched = not self.watched
        self._update_watch_button()

    def _update_watch_button(self) -> None:
        self.watch_button.setText(
            "Remove from watchlist" if self.watched else "Add to watchlist"
        )

    def _request_tracking(self) -> None:
        self.track_requested = True
        self.accept()


def _explanation(point: MarketPoint, candidate: FlipCandidate | None) -> str:
    if candidate:
        return (
            f"Why it ranked: confidence {candidate.confidence}%, suggested quantity "
            f"{candidate.suggested_quantity:,}, and estimated batch profit "
            f"{_gp(candidate.potential_profit)} after tax. The displayed prices are completed "
            "transactions—not outstanding offers—so fills are not guaranteed."
        )
    if point.average_5m and point.average_1h:
        drift = abs(point.average_5m - point.average_1h) / point.average_1h * 100
        return (
            f"Recent price drift is approximately {drift:.2f}% between the five-minute and "
            "one-hour midpoint. This item does not currently qualify for the selected flipping "
            "strategy, but it can still be monitored on your watchlist."
        )
    return (
        "There is not enough aggregate price history to assess short-term stability. "
        "Treat the observed margin cautiously."
    )


def _gp(value: int | None) -> str:
    return f"{value:,} gp" if value is not None else "Not available"


def _number(value: float | None) -> str:
    return f"{value:,.1f} gp" if value is not None else "Not available"


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} sec"
    if seconds < 3_600:
        return f"{seconds // 60} min"
    return f"{seconds // 3_600} hr {seconds % 3_600 // 60} min"
