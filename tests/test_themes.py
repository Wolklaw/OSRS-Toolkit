from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractSpinBox

from osrs_toolkit.app import _PALETTES, MainWindow, SettingsDialog


def test_every_offered_theme_has_a_palette() -> None:
    assert SettingsDialog.THEMES == list(_PALETTES)


@pytest.mark.parametrize("theme", list(_PALETTES))
def test_applying_a_theme_fills_in_every_colour(window: MainWindow, theme: str) -> None:
    window._apply_theme(theme)

    stylesheet = window.styleSheet()
    assert "None" not in stylesheet
    assert ": ;" not in stylesheet
    assert ": }" not in stylesheet
    assert _PALETTES[theme].background in stylesheet


@pytest.mark.parametrize("theme", list(_PALETTES))
def test_a_theme_colours_the_cells_it_paints_itself(window: MainWindow, theme: str) -> None:
    window._apply_theme(theme)
    palette = _PALETTES[theme]

    assert window._profit_color == palette.profit
    assert window._loss_color == palette.loss
    assert window._muted_color == palette.muted
    assert window._link_color == palette.link
    assert window._warning_color == palette.bought
    assert set(window._journal_status_colors) == {
        "Planned",
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Completed (manual)",
        "Cancelled",
        "Supplies",
    }


def test_an_unknown_saved_theme_falls_back_to_dark(window: MainWindow) -> None:
    window._apply_theme("Falador")

    assert window._profit_color == _PALETTES["Dark"].profit
    assert _PALETTES["Dark"].background in window.styleSheet()


@pytest.mark.parametrize("theme", list(_PALETTES))
def test_the_gp_fields_are_painted_like_the_spin_boxes_beside_them(
    window: MainWindow, theme: str
) -> None:
    window._apply_theme(theme)

    assert _field_pixel(window.cash) == _field_pixel(window.slots)


def _field_pixel(field: QAbstractSpinBox) -> int:
    image = field.grab().toImage()
    return image.pixel(6, image.height() // 2)
