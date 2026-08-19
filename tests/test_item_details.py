"""ItemDetailsDialog's Price history tab: lazy fetch and chart population.

Fetching is triggered only by actually opening the tab, so constructing the dialog and
inspecting the Overview tab never touches the network. Tests that do open the tab pass a
stub client instead of a real ``WikiMarketClient``, so the real background thread this
exercises completes against fabricated data rather than a genuine, possibly slow or
unavailable, network call.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from osrs_toolkit.item_details import ItemDetailsDialog
from osrs_toolkit.models import ItemMapping, MarketPoint, TimeseriesPoint


class _StubClient:
    """Answers instantly instead of making a real request, so the worker thread this
    dialog spins up on opening the history tab finishes on its own without network I/O."""

    def __init__(self, points: list[TimeseriesPoint] | None = None) -> None:
        self.points = points or []

    def fetch_timeseries(self, item_id: int, timestep: str = "6h") -> list[TimeseriesPoint]:
        return self.points


def _dialog(qt_app: QApplication, client: object | None = None) -> ItemDetailsDialog:
    item = ItemMapping(1234, "Dragon bones", False, 100, None)
    point = MarketPoint(
        item_id=1234,
        high=2_600,
        low=2_500,
        high_time=1_700_000_000,
        low_time=1_700_000_000,
        volume_5m=100,
        volume_1h=1_000,
    )
    dialog = ItemDetailsDialog(item, point, None, False, client or _StubClient(), None)
    qt_app.processEvents()
    return dialog


def _wait_for_thread(qt_app: QApplication, dialog: ItemDetailsDialog, timeout_ms: int = 2_000) -> None:
    thread = dialog._history_thread
    if thread is None:
        return
    elapsed = 0
    while thread.isRunning() and elapsed < timeout_ms:
        qt_app.processEvents()
        thread.wait(10)
        elapsed += 10


def test_constructing_the_dialog_never_starts_a_background_fetch(qt_app: QApplication) -> None:
    dialog = _dialog(qt_app)

    assert dialog._history_requested is False
    assert dialog._history_thread is None
    assert dialog.history_view.isHidden()


def test_opening_the_history_tab_fetches_and_populates_the_chart(qt_app: QApplication) -> None:
    points = [TimeseriesPoint(1_700_000_000, 2_500, 2_400)]
    dialog = _dialog(qt_app, _StubClient(points))

    dialog._tab_changed(dialog._history_tab_index)
    _wait_for_thread(qt_app, dialog)
    qt_app.processEvents()

    assert dialog._history_requested is True
    assert dialog.history_view.isHidden() is False


def test_reselecting_the_history_tab_does_not_refetch(qt_app: QApplication) -> None:
    dialog = _dialog(qt_app)

    dialog._tab_changed(dialog._history_tab_index)
    _wait_for_thread(qt_app, dialog)
    first_thread = dialog._history_thread
    dialog._tab_changed(0)
    dialog._tab_changed(dialog._history_tab_index)

    assert dialog._history_thread is first_thread


def test_a_populated_history_shows_the_chart(qt_app: QApplication) -> None:
    dialog = _dialog(qt_app)
    points = [
        TimeseriesPoint(1_700_000_000, 2_500, 2_400),
        TimeseriesPoint(1_700_021_600, 2_550, 2_420),
    ]

    dialog._history_loaded(points)

    assert dialog.history_view.isHidden() is False
    assert dialog.history_status.isHidden() is True
    assert len(dialog.history_chart.series()) == 2


def test_points_with_no_usable_price_show_an_empty_state(qt_app: QApplication) -> None:
    dialog = _dialog(qt_app)

    dialog._history_loaded([TimeseriesPoint(1_700_000_000, None, None)])

    assert dialog.history_view.isHidden() is True
    assert "No price history" in dialog.history_status.text()


def test_a_failed_fetch_reports_the_error_without_crashing(qt_app: QApplication) -> None:
    dialog = _dialog(qt_app)

    dialog._history_failed("network unavailable")

    assert "network unavailable" in dialog.history_status.text()
    assert dialog.history_view.isHidden()
