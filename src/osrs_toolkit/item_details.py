from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from osrs_toolkit.models import FlipCandidate, ItemMapping, MarketPoint
from osrs_toolkit.ranking import ge_tax, offer_targets


class ItemDetailsDialog(QDialog):
    def __init__(
        self,
        item: ItemMapping,
        point: MarketPoint,
        candidate: FlipCandidate | None,
        watched: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.watched = watched
        self.track_requested = False
        self.setWindowTitle(item.name)
        self.setMinimumSize(570, 520)
        layout = QVBoxLayout(self)

        title = QLabel(item.name, objectName="title")
        layout.addWidget(title)
        descriptor = "Members item" if item.members else "Free-to-play item"
        layout.addWidget(QLabel(f"{descriptor} • Item ID {item.item_id}", objectName="muted"))

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
        layout.addLayout(metrics)

        explanation = QLabel(_explanation(point, candidate), objectName="recommendation")
        explanation.setWordWrap(True)
        explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(explanation)

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
