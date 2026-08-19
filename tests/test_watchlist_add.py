"""Adding an item to the Watchlist directly from its own tab.

Previously the only way onto the watchlist was double-clicking a row in GE Flipper or Alch
Finder and toggling "watched" in the item details dialog — these tests cover the Watchlist
tab's own add-by-name field instead.
"""

from __future__ import annotations

from osrs_toolkit.app import MainWindow
from osrs_toolkit.models import ItemMapping


def _seed_mappings(window: MainWindow) -> None:
    window._mappings = {
        453: ItemMapping(453, "Coal", False, 13_000, 34),
        4_151: ItemMapping(4_151, "Abyssal whip", False, 70, 1_200),
    }


def test_adding_an_item_by_exact_name_puts_it_on_the_watchlist(window: MainWindow) -> None:
    _seed_mappings(window)

    window.watchlist_add_field.setText("Coal")
    window._add_watchlist_item()

    assert window._watchlist == {453}
    assert window.watchlist_add_field.text() == ""
    assert window.watchlist_add_status.isHidden()


def test_matching_is_case_insensitive(window: MainWindow) -> None:
    _seed_mappings(window)

    window.watchlist_add_field.setText("aByssAL whIP")
    window._add_watchlist_item()

    assert window._watchlist == {4_151}


def test_unknown_item_name_reports_an_error_and_changes_nothing(window: MainWindow) -> None:
    _seed_mappings(window)

    window.watchlist_add_field.setText("Not a real item")
    window._add_watchlist_item()

    assert window._watchlist == set()
    assert not window.watchlist_add_status.isHidden()
    assert "Not a real item" in window.watchlist_add_status.text()


def test_adding_an_already_watched_item_is_a_no_op_with_a_status_message(
    window: MainWindow,
) -> None:
    _seed_mappings(window)
    window._watchlist = {453}

    window.watchlist_add_field.setText("Coal")
    window._add_watchlist_item()

    assert window._watchlist == {453}
    assert not window.watchlist_add_status.isHidden()
    assert "already" in window.watchlist_add_status.text().lower()


def test_blank_input_does_nothing(window: MainWindow) -> None:
    _seed_mappings(window)

    window.watchlist_add_field.setText("   ")
    window._add_watchlist_item()

    assert window._watchlist == set()
    assert window.watchlist_add_status.isHidden()


def test_completer_offers_every_mapped_item_name(window: MainWindow) -> None:
    _seed_mappings(window)

    window._refresh_watchlist_completer()

    model = window.watchlist_add_completer.model()
    names = {model.data(model.index(row, 0)) for row in range(model.rowCount())}
    assert names == {"Coal", "Abyssal whip"}
