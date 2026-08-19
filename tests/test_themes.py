"""Every theme offered in Settings must produce a complete stylesheet.

A theme is a table of colours pushed through one f-string, so the failure worth catching
is a theme that is offered but half-defined: a name in the dropdown with no palette behind
it, or a palette missing a colour some rule needs. Both leave the app running and looking
wrong rather than raising, which is exactly the kind of thing that ships unnoticed.
"""

from __future__ import annotations

import pytest

from osrs_toolkit.app import _PALETTES, MainWindow, SettingsDialog


def test_every_offered_theme_has_a_palette() -> None:
    assert SettingsDialog.THEMES == list(_PALETTES)


@pytest.mark.parametrize("theme", list(_PALETTES))
def test_applying_a_theme_fills_in_every_colour(window: MainWindow, theme: str) -> None:
    window._apply_theme(theme)

    stylesheet = window.styleSheet()
    # An f-string given a missing attribute would raise, but one given an empty string
    # quietly produces "color: ;" — a rule Qt drops, leaving that element unthemed.
    assert "None" not in stylesheet
    assert ": ;" not in stylesheet
    assert ": }" not in stylesheet
    assert _PALETTES[theme].background in stylesheet


@pytest.mark.parametrize("theme", list(_PALETTES))
def test_a_theme_colours_the_cells_it_paints_itself(window: MainWindow, theme: str) -> None:
    """Table cells are coloured from Python, not the stylesheet, so they need the switch
    to reach the attributes the renderers read."""
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
    """A settings file written by a newer version can name a theme this one does not have."""
    window._apply_theme("Falador")

    assert window._profit_color == _PALETTES["Dark"].profit
    assert _PALETTES["Dark"].background in window.styleSheet()
