"""Drives the real MainWindow._render_performance / savings-goal widgets, the way
test_journal_summary.py drives _render_journal — the goal is persisted in QSettings
(StubSettings in these tests, via the `window` fixture's monkeypatch), not the journal
database, so tests read/write it directly rather than driving the modal SavingsGoalDialog.

Settings are written through ``app_module.QSettings()`` rather than importing StubSettings
directly from conftest — pytest loads conftest.py itself under a different module identity
than a plain ``import tests.conftest`` would, so the two would otherwise be separate classes
with separate backing dicts and silently never see each other's writes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QMessageBox

import osrs_toolkit.app as app_module
from osrs_toolkit.app import MainWindow, _format_eta, _format_goal_percent


def _set_goal(label: str, target: int, created_at: str) -> None:
    settings = app_module.QSettings()
    settings.setValue("savings_goal/label", label)
    settings.setValue("savings_goal/target", target)
    settings.setValue("savings_goal/created_at", created_at)


def test_no_goal_shows_the_empty_state(window: MainWindow) -> None:
    window._render_performance()

    assert window.savings_goal_label.text() == "No savings goal set yet."
    assert window.savings_goal_progress.isHidden() is True
    assert window.savings_goal_clear_button.isHidden() is True
    assert window.savings_goal_set_button.text() == "Set goal"


def test_a_goal_shows_progress_from_realized_profit_after_it_started(
    window: MainWindow,
) -> None:
    _set_goal("Twisted bow", 1_000_000, "2026-08-01T00:00:00+00:00")
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(100, 2_500)], [(100, 2_000)]
    )

    window._render_performance()

    assert "Twisted bow" in window.savings_goal_label.text()
    # Locks down the exact rendering: _gp() already appends " gp", so a naive f-string
    # that also appends " gp" after it would silently double up as "... gp gp ...".
    assert "gp gp" not in window.savings_goal_label.text()
    assert "45,000 gp / 1,000,000 gp" in window.savings_goal_label.text()
    assert window.savings_goal_progress.isHidden() is False
    assert window.savings_goal_clear_button.isHidden() is False
    assert window.savings_goal_set_button.text() == "Edit goal"
    assert window.savings_goal_progress.value() > 0


def test_profit_before_the_goal_started_does_not_count(window: MainWindow) -> None:
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(100, 2_500)], [(100, 2_000)]
    )
    with window._journal._connect() as connection:
        connection.execute(
            "UPDATE tracked_trades SET completed_at = ? WHERE position_id = ?",
            ("2020-01-01T00:00:00+00:00", position_id),
        )
    _set_goal("Twisted bow", 1_000_000, datetime.now(UTC).isoformat())

    window._render_performance()

    assert window.savings_goal_progress.value() == 0


def test_a_reached_goal_says_so(window: MainWindow) -> None:
    _set_goal("Cheap goal", 1, "2026-08-01T00:00:00+00:00")
    position_id = window._journal.track(1234, "Dragon bones", 100, 2_000, 2_500)
    window._journal.update_tracked(
        position_id, "Completed", None, None, [(100, 2_500)], [(100, 2_000)]
    )

    window._render_performance()

    assert "reached" in window.savings_goal_label.text()
    assert window.savings_goal_progress.value() == 100


def test_clearing_the_goal_resets_the_display(window: MainWindow, monkeypatch) -> None:
    _set_goal("Twisted bow", 1_000_000, "2026-08-01T00:00:00+00:00")
    window._render_performance()
    assert window.savings_goal_label.text() != "No savings goal set yet."
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes
    )

    window._clear_savings_goal()

    assert window.savings_goal_label.text() == "No savings goal set yet."
    assert window.savings_goal_progress.isHidden() is True
    assert window.savings_goal_clear_button.isHidden() is True


def test_declining_the_clear_confirmation_leaves_the_goal_alone(
    window: MainWindow, monkeypatch
) -> None:
    _set_goal("Twisted bow", 1_000_000, "2026-08-01T00:00:00+00:00")
    window._render_performance()
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.No
    )

    window._clear_savings_goal()

    assert "Twisted bow" in window.savings_goal_label.text()


# --- Formatting helpers -----------------------------------------------------------------


def test_a_tiny_but_real_sliver_of_progress_does_not_read_as_zero() -> None:
    """Regression: 426,880 / 1,200,000,000 gp rounded straight to "0%", which reads as no
    progress at all against a large target rather than a genuine (if tiny) amount."""
    assert _format_goal_percent(0.0356) == "<1%"


def test_zero_progress_still_reads_as_zero() -> None:
    assert _format_goal_percent(0.0) == "0%"


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(1.4, "1%"), (50.0, "50%"), (100.0, "100%")],
)
def test_ordinary_percentages_round_to_whole_numbers(percent: float, expected: str) -> None:
    assert _format_goal_percent(percent) == expected


def test_eta_under_a_day_reads_as_less_than_a_day() -> None:
    assert _format_eta(0.4) == "less than a day"


def test_eta_under_a_year_reads_in_days() -> None:
    assert _format_eta(45) == "~45 days"


def test_eta_over_a_year_reads_in_years() -> None:
    """Regression: a huge target against a modest profit rate produced "~19671 days left",
    which doesn't parse at a glance the way a years figure does."""
    assert _format_eta(19_671) == "~54 years"


def test_eta_just_over_a_year_keeps_a_decimal() -> None:
    assert _format_eta(400) == "~1.1 years"
