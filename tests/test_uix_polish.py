"""Three places the app knew something and did not say it out loud.

Confidence was the one figure on the GE Flipper telling you how much to trust the rest, and
it was drawn in flat text among columns of confident green. The Needs attention card counted
rows it gave you no way to reach. And every table cell repeated its own text back at you on
hover, which was invisible until the tooltips were themed and then read as the app having
something to say.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QToolTip

from osrs_toolkit.app import MainWindow, confidence_standing
from osrs_toolkit.models import FlipCandidate, MarketPoint

# --- confidence -------------------------------------------------------------------------


def test_a_score_is_read_against_the_floor_it_was_judged_by() -> None:
    """58% is a poor showing under Overnight's 65 and a comfortable one under Quick's 45,
    so the raw number cannot be graded on its own."""
    assert confidence_standing(58, 45) > confidence_standing(58, 55)
    assert confidence_standing(45, 45) == 0.0
    assert confidence_standing(100, 45) == 1.0


def test_a_score_below_the_floor_does_not_go_negative() -> None:
    assert confidence_standing(30, 65) == 0.0


def _flip(confidence: int, item_id: int = 1_000) -> FlipCandidate:
    return FlipCandidate(
        item_id=item_id,
        name=f"Item {item_id}",
        buy_price=1_000,
        sell_price=1_400,
        tax=28,
        profit_each=372,
        roi=37.2,
        hourly_volume=5_000,
        projected_volume=20_000,
        buy_limit=125,
        suggested_quantity=10,
        capital_required=10_000,
        potential_profit=3_720,
        confidence=confidence,
        age_seconds=120,
        score=9.0,
    )


def _confidence_cell(window: MainWindow, row: int):
    return window.flip_table.item(row, 9)


def test_a_thin_score_is_drawn_as_a_warning(window: MainWindow) -> None:
    """Balanced filters at 55, so 60 has barely cleared it — and every other figure on the
    row is shouting a profit."""
    window.strategy.setCurrentText("Balanced (1–4h)")
    window._flips = [_flip(60)]
    window._render_flips()

    cell = _confidence_cell(window, 0)
    assert cell.text() == "60%"
    assert cell.foreground().color().name() == window._warning_color
    assert "treat it as thin" in cell.toolTip()


def test_a_strong_score_is_drawn_as_one(window: MainWindow) -> None:
    window.strategy.setCurrentText("Balanced (1–4h)")
    window._flips = [_flip(95)]
    window._render_flips()

    cell = _confidence_cell(window, 0)
    assert cell.foreground().color().name() == window._profit_color
    assert "Strong" in cell.toolTip()


def test_the_same_score_is_graded_by_the_strategy_in_force(window: MainWindow) -> None:
    """The point of grading against the floor: 70 is comfortable under Quick and thin under
    Overnight, and the colour has to move even though the number does not."""
    window._flips = [_flip(70)]
    window.strategy.setCurrentText("Quick (up to 1h)")
    window._render_flips()
    assert _confidence_cell(window, 0).foreground().color().name() != window._warning_color

    window.strategy.setCurrentText("Overnight (8–12h)")
    window._render_flips()
    assert _confidence_cell(window, 0).foreground().color().name() == window._warning_color


def test_the_recommendation_card_grades_it_the_same_way(window: MainWindow) -> None:
    """Two surfaces showing one score must not disagree about whether it is any good."""
    window.strategy.setCurrentText("Balanced (1–4h)")
    window._portfolio = [_flip(60), _flip(95, item_id=1_001)]

    html = window._portfolio_html()

    assert f'style="color:{window._warning_color}">60%' in html
    assert f'style="color:{window._profit_color}">95%' in html


# --- the Needs attention card -----------------------------------------------------------


def _stale_listing(window: MainWindow, item_id: int, name: str) -> int:
    """A position asking well above what the market will now support."""
    position_id = window._journal.track(item_id, name, 70, 7_125, 8_180)
    window._journal.update_tracked(
        position_id, "Listed for sale", 7_125, None, None, [(70, 7_125)]
    )
    window._points.append(
        MarketPoint(
            item_id=item_id,
            high=7_000,
            low=6_800,
            high_time=1_700_000_000,
            low_time=1_700_000_000,
            volume_5m=1_000,
            volume_1h=10_000,
        )
    )
    return position_id


def test_the_card_offers_itself_only_when_it_has_something_to_point_at(
    window: MainWindow,
) -> None:
    """The hand cursor is the whole affordance, so a card reading zero must not show one."""
    window._render_journal()
    assert window.journal_attention.cursor().shape() == Qt.CursorShape.ArrowCursor

    _stale_listing(window, 1_234, "Antidote++(3)")
    window._render_journal()

    assert window.journal_attention.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_clicking_the_card_lights_every_row_it_counts(window: MainWindow) -> None:
    """A count is a count: pointing at one of two answers half the question."""
    first = _stale_listing(window, 1_234, "Antidote++(3)")
    second = _stale_listing(window, 5_678, "Ranarr weed")
    window._render_journal()

    window.journal_attention.clicked.emit()

    assert window._journal_flasher.is_lit(first)
    assert window._journal_flasher.is_lit(second)


def test_clicking_the_card_widens_a_filter_that_was_hiding_the_rows(
    window: MainWindow,
) -> None:
    position_id = _stale_listing(window, 1_234, "Antidote++(3)")
    window.journal_status_filter.setCurrentText("Completed")
    window._render_journal()

    window.journal_attention.clicked.emit()

    assert window._journal_flasher.is_lit(position_id)
    assert window.journal_status_filter.currentText() == "All statuses"


def test_a_card_with_nothing_to_show_does_nothing_when_clicked(window: MainWindow) -> None:
    window._render_journal()

    window.journal_attention.clicked.emit()

    assert window._journal_flasher.is_lit(1) is False


# --- tooltips that only repeat the cell --------------------------------------------------


def _hover(window: MainWindow, row: int, column: int) -> str:
    """Move the cursor over a cell, the same way an arriving mouse now triggers a tooltip.

    Empty means no tooltip appeared. Tooltips no longer wait for Qt's native hold-still
    timer — see ``ResponsiveTableWidget.mouseMoveEvent`` — so a move is what shows one.
    """
    table = window.journal_table
    table._tooltip_index = table.model().index(-1, -1)  # force the move to be seen as new
    QToolTip.hideText()
    centre = table.visualRect(table.model().index(row, column)).center()
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(centre),
        table.viewport().mapToGlobal(centre),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    table.mouseMoveEvent(event)
    QApplication.processEvents()
    return QToolTip.text() if QToolTip.isVisible() else ""


def test_a_cell_that_fits_does_not_repeat_itself(window: MainWindow) -> None:
    window._journal.track(1_234, "Antidote++(3)", 70, 7_125, 8_180)
    window._render_journal()
    window.journal_table.setColumnWidth(3, 400)

    assert window.journal_table.item(0, 3).toolTip() == window.journal_table.item(0, 3).text()
    assert _hover(window, 0, 3) == "", "a quantity that fits has nothing to add"


def test_a_cell_too_narrow_to_read_still_offers_its_text(window: MainWindow) -> None:
    window._journal.track(1_234, "Antidote++(3)", 70, 7_125, 8_180)
    window._render_journal()
    window.journal_table.setColumnWidth(2, 30)

    assert _hover(window, 0, 2) == "Antidote++(3)", "an elided name is what tooltips are for"


def test_a_deliberate_explanation_always_shows(window: MainWindow) -> None:
    """The P/L tooltip explains something the cell does not say, so it is never suppressed
    however much room the column has."""
    window._journal.track(1_234, "Antidote++(3)", 70, 7_125, 8_180)
    window._render_journal()
    window.journal_table.setColumnWidth(8, 600)

    profit_cell = window.journal_table.item(0, 8)
    assert profit_cell.toolTip() != profit_cell.text()
    assert _hover(window, 0, 8) == profit_cell.toolTip()


def test_a_tooltip_needs_no_dwell_time(window: MainWindow) -> None:
    """The bug this file guards: Qt's native tooltip only fires once the cursor stops
    moving over a cell and holds still, which made the narrow ⚠ glyph a fiddly target —
    a single move that lands on it and keeps moving is what a real hover looks like, and
    that alone has to be enough."""
    window._journal.track(1_234, "Antidote++(3)", 70, 7_125, 8_180)
    window._render_journal()

    table = window.journal_table
    table._tooltip_index = table.model().index(-1, -1)
    QToolTip.hideText()
    centre = table.visualRect(table.model().index(0, 3)).center()
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(centre),
        table.viewport().mapToGlobal(centre),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    table.mouseMoveEvent(event)
    # No qt_app.processEvents() loop, no wait: this is the whole point being tested.

    assert QToolTip.isVisible()


def test_leaving_the_table_forgets_the_last_cell_shown(window: MainWindow) -> None:
    """Leaving the table has to clear what it remembers hovering, the same way the old
    per-event mechanism started fresh on every request — otherwise moving off the table
    and straight back onto the same cell would read as "nothing changed" and stay quiet.

    ``QToolTip``'s own global visibility flag is a native popup with timing of its own
    that this offscreen test platform does not reproduce reliably, so this checks the
    state the table actually owns rather than racing that singleton.
    """
    window._journal.track(1_234, "Antidote++(3)", 70, 7_125, 8_180)
    window._render_journal()
    table = window.journal_table
    assert _hover(window, 0, 2)
    assert table._tooltip_index.isValid()

    table.leaveEvent(QEvent(QEvent.Type.Leave))

    assert table._tooltip_index.isValid() is False
