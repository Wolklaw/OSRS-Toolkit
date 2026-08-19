"""Drives the real GE Flipper recommendation card.

The card holds anything from one offer to a full eight, and it shares the page with the
flip table, so what is asserted here is the trade-off between the two: show the whole plan
when the window can hold it, and scroll rather than crowd the table out when it cannot.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import MainWindow
from osrs_toolkit.models import FlipCandidate

NAMES = [
    "Dragon platebody",
    "Bandos chestplate",
    "White platelegs",
    "Amulet of fury",
    "Abyssal whip",
    "Toxic blowpipe (empty)",
    "Ancient ceremonial legs",
    "Rune platebody",
]


def _candidate(index: int, name: str) -> FlipCandidate:
    buy = 4_589 + index * 1_311
    sell = int(buy * 1.4)
    quantity = 8 + index
    return FlipCandidate(
        item_id=1_000 + index,
        name=name,
        buy_price=buy,
        sell_price=sell,
        tax=int(sell * 0.02),
        profit_each=sell - buy,
        roi=12.5 + index,
        hourly_volume=5_000,
        projected_volume=20_000,
        buy_limit=125,
        suggested_quantity=quantity,
        capital_required=buy * quantity,
        potential_profit=(sell - buy) * quantity,
        confidence=67 - index * 3,
        age_seconds=120,
        score=9.0 - index,
    )


def _plan(window: MainWindow, qt_app: QApplication, offers: int, *, height: int) -> None:
    """Put ``offers`` recommendations on a shown window of ``height`` and let Qt settle.

    Each round of layout only reacts to the last one — the card measures its contents,
    which resizes it, which gives the page a new height to share out — so the geometry
    asserted on is the one a few rounds later, not the one straight after the render.
    """
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index("GE Flipper"))
    window.show()
    window.resize(1280, height)
    window._portfolio = [_candidate(index, NAMES[index]) for index in range(offers)]
    window._render_flips()
    for _ in range(6):
        qt_app.processEvents()


def test_a_full_plan_is_shown_in_one_piece(qt_app: QApplication, window: MainWindow) -> None:
    _plan(window, qt_app, 8, height=980)

    rows = window.flip_recommendation_rows
    assert rows.verticalScrollBar().maximum() == 0, "all eight offers should fit unscrolled"
    for name in NAMES:
        assert name in window.flip_recommendation.text()


def test_a_short_window_scrolls_the_plan_rather_than_burying_the_table(
    qt_app: QApplication, window: MainWindow
) -> None:
    """The card giving way is the whole point of the ceiling: eight offers at full height
    would leave the flip table below with nothing but its header."""
    _plan(window, qt_app, 8, height=window.minimumHeight())

    rows = window.flip_recommendation_rows
    assert rows.verticalScrollBar().maximum() > 0, "the plan should scroll, not be cut off"
    assert window.flip_table.height() > 150, "the table was squeezed out of usefulness"


def test_a_smaller_plan_gives_its_room_back(qt_app: QApplication, window: MainWindow) -> None:
    _plan(window, qt_app, 8, height=980)
    full = window.flip_recommendation_rows.height()

    _plan(window, qt_app, 2, height=980)

    assert window.flip_recommendation_rows.height() < full
    assert window.flip_recommendation_rows.verticalScrollBar().maximum() == 0


def test_nothing_worth_recommending_leaves_only_the_reason(
    qt_app: QApplication, window: MainWindow
) -> None:
    _plan(window, qt_app, 0, height=980)

    assert "No opportunity" in window.flip_recommendation_headline.text()
    assert window.flip_recommendation_rows.isHidden() is True
    assert window.flip_recommendation_note.isHidden() is True
    assert window.track_top_button.isEnabled() is False
