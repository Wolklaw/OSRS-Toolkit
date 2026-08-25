"""Everything a table row can do, reachable without the mouse and without remembering.

Every table in this app opened a row on double-click alone: walk one with the arrow keys and
press Enter and nothing happened, on pages the Ctrl+number shortcuts exist so the keyboard
can reach them in the first place. Nothing answered a right-click either, so the verbs for a
row lived only in buttons somewhere else on the page — buttons that stayed enabled with no
row selected and answered the click with "select a row first".
"""

from __future__ import annotations

import os
from typing import ClassVar

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QMessageBox, QTableWidgetItem

from osrs_toolkit.app import _PVM_GUIDE_COLUMN, MainWindow
from osrs_toolkit.models import FlipCandidate

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _flip(item_id: int = 1_000, name: str = "Abyssal whip") -> FlipCandidate:
    return FlipCandidate(
        item_id=item_id,
        name=name,
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
        confidence=80,
        age_seconds=120,
        score=9.0,
    )


def _menu_labels(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


# --- Enter opens a row ------------------------------------------------------------------


def test_enter_opens_the_row_the_keyboard_is_on(window: MainWindow) -> None:
    window._flips = [_flip()]
    window._render_flips()
    opened: list[tuple[int, int]] = []
    window.flip_table.rowActivated.connect(lambda row, column: opened.append((row, column)))
    window.flip_table.setCurrentCell(0, 3)

    QTest.keyClick(window.flip_table, Qt.Key.Key_Return)

    assert opened == [(0, 3)]


def test_enter_on_an_empty_table_opens_nothing(window: MainWindow) -> None:
    opened: list[tuple[int, int]] = []
    window.flip_table.rowActivated.connect(lambda row, column: opened.append((row, column)))

    QTest.keyClick(window.flip_table, Qt.Key.Key_Return)

    assert opened == []


def test_a_double_click_does_not_also_count_as_a_keyboard_open(window: MainWindow) -> None:
    """The reason the keyboard got a signal of its own rather than Qt's ``activated``: on
    Windows that one fires on double-click too, and every row would have opened twice."""
    window._flips = [_flip()]
    window._render_flips()
    opened: list[tuple[int, int]] = []
    window.flip_table.rowActivated.connect(lambda row, column: opened.append((row, column)))

    window.flip_table.cellDoubleClicked.emit(0, 0)

    assert opened == []


def test_a_modified_return_is_left_to_the_table(window: MainWindow) -> None:
    """Ctrl+Enter and friends belong to whatever else wants them, not to opening a row."""
    window._flips = [_flip()]
    window._render_flips()
    opened: list[tuple[int, int]] = []
    window.flip_table.rowActivated.connect(lambda row, column: opened.append((row, column)))
    window.flip_table.setCurrentCell(0, 0)

    QTest.keyClick(window.flip_table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    assert opened == []


def _listens_for_enter(table) -> bool:  # type: ignore[no-untyped-def]
    meta = table.metaObject()
    return table.isSignalConnected(meta.method(meta.indexOfSignal("rowActivated(int,int)")))


def test_every_table_of_rows_answers_enter(window: MainWindow) -> None:
    """A page where Enter does nothing is a page the keyboard can only read."""
    for table in (
        window.flip_table,
        window.watchlist_table,
        window.journal_table,
        window.alch_table,
        window.skill_table,
        window.pvm_table,
    ):
        assert _listens_for_enter(table)


# --- right-click menus ------------------------------------------------------------------


def test_a_market_row_offers_the_verbs_that_apply_to_it(window: MainWindow) -> None:
    window._flips = [_flip()]
    window._render_flips()
    menu = QMenu()

    window._build_market_row_menu(window.flip_table)(menu, 0)

    assert _menu_labels(menu) == [
        "View details…",
        "Track this flip",
        "Add to watchlist",
        "Copy “Abyssal whip”",
    ]


def test_a_watched_item_is_offered_the_other_direction(window: MainWindow) -> None:
    """The one entry that has to read the current state rather than assume it."""
    window._flips = [_flip()]
    window._render_flips()
    window._watchlist.add(1_000)
    menu = QMenu()

    window._build_market_row_menu(window.flip_table)(menu, 0)

    assert "Remove from watchlist" in _menu_labels(menu)
    assert "Add to watchlist" not in _menu_labels(menu)


def test_an_item_with_no_flip_is_not_offered_a_flip_to_track(window: MainWindow) -> None:
    """The Watchlist holds items the current strategy found no margin for; there is nothing
    to track for those, and an entry that silently does nothing is worse than no entry."""
    window._flips = [_flip()]
    window._render_flips()
    window._flips = []
    menu = QMenu()

    window._build_market_row_menu(window.flip_table)(menu, 0)

    assert "Track this flip" not in _menu_labels(menu)


def test_copying_a_row_puts_the_item_name_where_the_game_can_take_it(
    window: MainWindow,
) -> None:
    window._flips = [_flip()]
    window._render_flips()
    menu = QMenu()
    window._build_market_row_menu(window.flip_table)(menu, 0)

    next(action for action in menu.actions() if action.text().startswith("Copy")).trigger()

    assert QGuiApplication.clipboard().text() == "Abyssal whip"


def test_a_journal_row_offers_its_own_buttons(window: MainWindow) -> None:
    window._journal.track(1_000, "Abyssal whip", 10, 1_000, 1_400)
    window._render_journal()
    menu = QMenu()

    window._build_journal_row_menu(menu, 0)

    assert _menu_labels(menu) == ["Update trade…", "Delete trade…", "Copy “Abyssal whip”"]


def test_a_row_with_a_guide_is_offered_it(window: MainWindow) -> None:
    """PvM and Skilling rows link out to the wiki from one cell of their own."""
    window.pvm_table.setRowCount(0)
    window.pvm_table.setRowCount(1)
    window.pvm_table.setItem(0, 0, QTableWidgetItem("Zulrah"))
    guide = QTableWidgetItem("Wiki")
    guide.setData(Qt.ItemDataRole.UserRole, "https://oldschool.runescape.wiki/w/Zulrah")
    window.pvm_table.setItem(0, _PVM_GUIDE_COLUMN, guide)
    menu = QMenu()

    window._build_guide_row_menu(window.pvm_table, _PVM_GUIDE_COLUMN, window._open_pvm_guide)(
        menu, 0
    )

    assert _menu_labels(menu) == ["Open wiki guide", "Copy “Zulrah”"]


def test_a_row_with_no_guide_is_not_offered_one(window: MainWindow) -> None:
    """An entry that silently does nothing is worse than no entry at all."""
    window.pvm_table.setRowCount(0)
    window.pvm_table.setRowCount(1)
    window.pvm_table.setItem(0, 0, QTableWidgetItem("Zulrah"))
    menu = QMenu()

    window._build_guide_row_menu(window.pvm_table, _PVM_GUIDE_COLUMN, window._open_pvm_guide)(
        menu, 0
    )

    assert "Open wiki guide" not in _menu_labels(menu)


class _RecordingMenu:
    """Stands in for QMenu so the real right-click path can run without a modal menu."""

    opened: ClassVar[list[list[str]]] = []

    def __init__(self, _parent: object = None) -> None:
        self._labels: list[str] = []

    def addAction(self, text: str, _slot: object = None) -> None:
        self._labels.append(text)

    def addSeparator(self) -> None:
        pass

    def isEmpty(self) -> bool:
        return not self._labels

    def exec(self, _position: object) -> None:
        _RecordingMenu.opened.append(list(self._labels))


def test_right_clicking_a_row_selects_it_and_opens_a_menu(window: MainWindow, monkeypatch) -> None:
    """Through the real signal, not the builder: Qt does not reliably move the selection on
    a right-press, so an action reading "the selected trade" could act on another row."""
    window._flips = [_flip(), _flip(1_001, "Dragon bones")]
    window._render_flips()
    _RecordingMenu.opened.clear()
    monkeypatch.setattr("osrs_toolkit.app.QMenu", _RecordingMenu)
    second_row = window.flip_table.visualRect(window.flip_table.model().index(1, 0)).center()

    window.flip_table.customContextMenuRequested.emit(second_row)

    assert window.flip_table.currentRow() == 1
    # Highlighted too, not just current: the row the menu is about has to be the row the
    # player can see it is about.
    assert window.flip_table.selectionModel().hasSelection()
    assert _RecordingMenu.opened and "View details…" in _RecordingMenu.opened[0]


def test_right_clicking_past_the_last_row_opens_nothing(window: MainWindow, monkeypatch) -> None:
    """Empty space below a table is not a row, and an empty menu is worse than none."""
    window._flips = [_flip()]
    window._render_flips()
    _RecordingMenu.opened.clear()
    monkeypatch.setattr("osrs_toolkit.app.QMenu", _RecordingMenu)

    window.flip_table.customContextMenuRequested.emit(QPoint(20, 4_000))

    assert _RecordingMenu.opened == []


def test_every_table_of_rows_answers_a_right_click(window: MainWindow) -> None:
    for table in (
        window.flip_table,
        window.watchlist_table,
        window.journal_table,
        window.alch_table,
        window.skill_table,
        window.pvm_table,
    ):
        assert table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


# --- buttons that follow the selection --------------------------------------------------


def test_the_row_buttons_start_out_of_reach(window: MainWindow) -> None:
    """Nothing is selected on a freshly opened journal, so nothing can act on a selection."""
    assert window._journal_row_buttons
    assert not any(button.isEnabled() for button in window._journal_row_buttons)


def test_selecting_a_row_puts_them_within_it(window: MainWindow) -> None:
    window._journal.track(1_000, "Abyssal whip", 10, 1_000, 1_400)
    window._render_journal()

    window.journal_table.selectRow(0)

    assert all(button.isEnabled() for button in window._journal_row_buttons)


def test_the_selection_going_away_takes_them_with_it(window: MainWindow) -> None:
    """A journal emptied by a status filter must not leave the buttons looking usable."""
    window._journal.track(1_000, "Abyssal whip", 10, 1_000, 1_400)
    window._render_journal()
    window.journal_table.selectRow(0)

    window.journal_table.clearSelection()

    assert not any(button.isEnabled() for button in window._journal_row_buttons)


def test_a_render_that_empties_the_table_disables_them(window: MainWindow) -> None:
    position_id = window._journal.track(1_000, "Abyssal whip", 10, 1_000, 1_400)
    window._render_journal()
    window.journal_table.selectRow(0)
    window._journal.delete_tracked(position_id)

    window._render_journal()

    assert not any(button.isEnabled() for button in window._journal_row_buttons)


# --- the Delete key ---------------------------------------------------------------------


def _delete_shortcuts(table) -> list[QShortcut]:  # type: ignore[no-untyped-def]
    wanted = QKeySequence(QKeySequence.StandardKey.Delete)
    return [shortcut for shortcut in table.findChildren(QShortcut) if shortcut.key() == wanted]


def test_delete_removes_the_selected_row_from_the_table_that_has_focus(
    window: MainWindow,
) -> None:
    for table in (window.journal_table,):
        shortcuts = _delete_shortcuts(table)
        assert len(shortcuts) == 1
        # Scoped to the widget, or Delete pressed anywhere in the window would reach into a
        # table on a page nobody is looking at.
        assert shortcuts[0].context() == Qt.ShortcutContext.WidgetShortcut


def test_delete_asks_before_anything_goes(window: MainWindow, monkeypatch) -> None:
    """The key is only a shortcut to the button, confirmation included."""
    window._journal.track(1_000, "Abyssal whip", 10, 1_000, 1_400)
    window._render_journal()
    window.journal_table.selectRow(0)
    asked: list[str] = []

    def refuse(*args: object, **_kwargs: object) -> object:
        asked.append(str(args[1]))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("osrs_toolkit.app.QMessageBox.question", refuse)

    _delete_shortcuts(window.journal_table)[0].activated.emit()

    assert asked == ["Delete trade"]
    assert len(window._journal.list_tracked()) == 1
