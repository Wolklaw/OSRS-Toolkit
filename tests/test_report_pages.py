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
