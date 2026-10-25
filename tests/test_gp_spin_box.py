from __future__ import annotations

import pytest
from PySide6.QtGui import QValidator
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import osrs_toolkit.app as app_module
from osrs_toolkit.app import (
    GP_FIELD_MAXIMUM,
    GpSpinBox,
    MainWindow,
    SavingsGoalDialog,
    _gp_digits,
)

MAX_CASH_STACK = 2_147_483_647
ABOVE_THE_OLD_CAP = 9_000_000_000


@pytest.fixture
def field(qt_app: QApplication) -> GpSpinBox:
    box = GpSpinBox()
    box.setRange(10_000, GP_FIELD_MAXIMUM)
    box.setValue(10_000_000)
    box.setSingleStep(1_000_000)
    yield box
    box.deleteLater()


def test_a_stack_above_the_old_cap_round_trips(field: GpSpinBox) -> None:
    field.setValue(ABOVE_THE_OLD_CAP)

    assert field.value() == ABOVE_THE_OLD_CAP
    assert field.lineEdit().text() == "9,000,000,000"


def test_the_whole_amount_reaches_whatever_is_listening(field: GpSpinBox) -> None:
    seen: list[int] = []
    field.valueChanged.connect(seen.append)

    field.setValue(ABOVE_THE_OLD_CAP)

    assert seen == [ABOVE_THE_OLD_CAP]


def test_a_value_only_announces_itself_when_it_moves(field: GpSpinBox) -> None:
    seen: list[int] = []
    field.valueChanged.connect(seen.append)

    field.setValue(10_000_000)

    assert seen == []


def test_the_value_is_clamped_into_range(field: GpSpinBox) -> None:
    field.setValue(1)
    assert field.value() == 10_000

    field.setValue(GP_FIELD_MAXIMUM * 2)
    assert field.value() == GP_FIELD_MAXIMUM


def test_stepping_moves_by_the_single_step(field: GpSpinBox) -> None:
    field.setValue(ABOVE_THE_OLD_CAP)

    field.stepBy(3)

    assert field.value() == ABOVE_THE_OLD_CAP + 3_000_000


def test_stepping_stops_at_the_ends_of_the_range(field: GpSpinBox) -> None:
    field.setValue(10_000)
    assert field.stepEnabled() == GpSpinBox.StepEnabledFlag.StepUpEnabled

    field.setValue(GP_FIELD_MAXIMUM)
    assert field.stepEnabled() == GpSpinBox.StepEnabledFlag.StepDownEnabled


def test_a_stack_above_the_old_cap_can_be_typed_in(field: GpSpinBox) -> None:
    field.lineEdit().clear()

    QTest.keyClicks(field.lineEdit(), "9000000000")

    assert field.value() == ABOVE_THE_OLD_CAP
    assert field.lineEdit().text() == "9,000,000,000"


def test_the_caret_keeps_its_place_as_separators_appear(field: GpSpinBox) -> None:
    field.lineEdit().clear()

    QTest.keyClicks(field.lineEdit(), "1234567")

    assert field.lineEdit().text() == "1,234,567"
    assert field.lineEdit().cursorPosition() == len("1,234,567")


def test_an_amount_still_below_the_minimum_is_not_acted_on(field: GpSpinBox) -> None:
    field.lineEdit().clear()

    QTest.keyClicks(field.lineEdit(), "5")

    assert field.value() == 10_000_000
    assert field.validate("5", 1)[0] == QValidator.State.Intermediate


def test_an_unfinished_amount_is_fixed_up_when_the_field_is_left(field: GpSpinBox) -> None:
    field.lineEdit().clear()
    QTest.keyClicks(field.lineEdit(), "5")

    field._commit()

    assert field.value() == 10_000
    assert field.lineEdit().text() == "10,000"


def test_clearing_the_field_keeps_the_amount_it_had(field: GpSpinBox) -> None:
    field.lineEdit().clear()

    field._commit()

    assert field.value() == 10_000_000
    assert field.lineEdit().text() == "10,000,000"


def test_anything_that_is_not_an_amount_is_rejected(field: GpSpinBox) -> None:
    assert field.validate("40m", 3)[0] == QValidator.State.Invalid
    assert field.validate("", 0)[0] == QValidator.State.Intermediate
    assert field.validate("50,000,000", 10)[0] == QValidator.State.Acceptable


@pytest.mark.parametrize(
    ("text", "suffix", "expected"),
    [
        ("9,000,000,000", "", "9000000000"),
        ("1,000 gp", " gp", "1000"),
        ("1,000 g", " gp", "1000"),
        ("1,000 ", " gp", "1000"),
        ("", " gp", ""),
        ("gp", " gp", ""),
        ("40m", "", None),
        ("1,000 px", " gp", None),
    ],
)
def test_the_digits_of_a_typed_amount(text: str, suffix: str, expected: str | None) -> None:
    assert _gp_digits(text, suffix) == expected


def test_the_flips_tab_cannot_hold_more_than_a_cash_stack(window: MainWindow) -> None:
    window.cash.setValue(ABOVE_THE_OLD_CAP)

    assert window.cash.value() == MAX_CASH_STACK


def test_an_alch_budget_is_capped_at_a_cash_stack(window: MainWindow) -> None:
    window.alch_budget.setValue(ABOVE_THE_OLD_CAP)

    window._alch_settings_changed(None)

    assert int(app_module.QSettings().value("alch/budget")) == MAX_CASH_STACK


def test_a_savings_goal_above_the_old_cap_reads_back_whole(window: MainWindow) -> None:
    settings = app_module.QSettings()
    settings.setValue("savings_goal/label", "Twisted bow")
    settings.setValue("savings_goal/target", ABOVE_THE_OLD_CAP)
    settings.setValue("savings_goal/created_at", "2026-08-01T00:00:00+00:00")

    goal = window._saved_savings_goal()

    assert goal is not None
    assert goal[1] == ABOVE_THE_OLD_CAP


def test_the_savings_goal_dialog_takes_a_target_above_the_old_cap(
    qt_app: QApplication,
) -> None:
    dialog = SavingsGoalDialog("Twisted bow", ABOVE_THE_OLD_CAP)

    assert dialog.goal_target.value() == ABOVE_THE_OLD_CAP
    assert dialog.goal_target.lineEdit().text() == "9,000,000,000 gp"

    dialog.deleteLater()
