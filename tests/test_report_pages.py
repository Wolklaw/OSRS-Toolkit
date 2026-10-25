from __future__ import annotations

import pytest

from osrs_toolkit.app import MainWindow
from osrs_toolkit.reports import REPORTS


def test_every_report_has_a_page_of_its_own(window: MainWindow) -> None:
    for key in REPORTS:
        assert key in MainWindow.NAV_ITEMS
    assert window.pages.count() == len(MainWindow.NAV_ITEMS)


@pytest.mark.parametrize("key", list(REPORTS))
def test_a_report_page_renders_without_a_character(window: MainWindow, key: str) -> None:
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index(key))

    assert window._report_bodies[key].count() > 0


def test_the_cash_stack_cannot_exceed_what_one_stack_can_hold(window: MainWindow) -> None:
    window.cash.setValue(10**12)

    assert window.cash.value() == 2_147_483_647


def test_the_sidebar_groups_every_page_exactly_once(window: MainWindow) -> None:
    grouped = [name for _group, names in MainWindow.NAV_GROUPS for name in names]

    assert grouped == MainWindow.NAV_ITEMS
    assert len(set(grouped)) == window.pages.count() == window.nav.count()


def test_each_group_is_captioned_on_its_first_page(window: MainWindow) -> None:
    from osrs_toolkit.app import NAV_GROUP_ROLE

    captioned = {
        window.nav.item(row).data(NAV_GROUP_ROLE): MainWindow.NAV_ITEMS[row]
        for row in range(window.nav.count())
        if window.nav.item(row).data(NAV_GROUP_ROLE)
    }

    assert captioned == {group: names[0] for group, names in MainWindow.NAV_GROUPS}


def test_every_flip_adds_the_rows_that_miss_the_bar_without_recommending_them(
    window: MainWindow,
) -> None:
    import time

    from osrs_toolkit.models import ItemMapping, MarketPoint

    now = int(time.time())
    mappings = {
        1: ItemMapping(1, "Fresh item", True, 100, None),
        2: ItemMapping(2, "Stale item", True, 100, None),
    }
    points = [
        MarketPoint(1, 1_200, 1_000, now - 60, now - 90, 50, 600, 1_090, 1_080),
        MarketPoint(2, 2_000, 1_000, now - 4_000, now - 4_000, 50, 600),
    ]
    window._mappings, window._points = mappings, points
    window.cash.setValue(100_000)
    window._rerank()
    window._render_flips()
    before = window.flip_table.rowCount()

    window.show_all_flips.setChecked(True)

    assert window.flip_table.rowCount() == before + 1
    assert all(candidate.meets_strategy for candidate in window._portfolio)
