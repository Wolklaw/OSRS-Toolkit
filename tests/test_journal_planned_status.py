from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from osrs_toolkit.app import MainWindow


class _CancellingDialog:
    STATUSES: ClassVar[list[str]] = [
        "Pending buy",
        "Bought",
        "Listed for sale",
        "Partially sold",
        "Completed",
        "Cancelled",
        "Supplies",
    ]

    def __init__(self, trade: object, _parent: object = None) -> None:
        self.status = type("_Combo", (), {"currentText": staticmethod(lambda: "Cancelled")})()
        quantity = trade.quantity
        self.quantity_acquired = type("_Spin", (), {"value": staticmethod(lambda: quantity)})()

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def sale_fills(self) -> list[tuple[int, int]]:
        return []

    def buy_fills(self) -> list[tuple[int, int]]:
        return []


def _connect(window: MainWindow, tmp_path: Path, slots: dict[str, object]) -> None:
    from osrs_toolkit.runelite_sync import RuneLiteSyncImporter

    root = tmp_path / "sync"
    (root / "state").mkdir(parents=True)
    (root / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": True,
                "account_hash": "abc123",
                "account_name": "Example Player",
            }
        ),
        encoding="utf-8",
    )
    (root / "state" / "abc123.json").write_text(json.dumps(slots), encoding="utf-8")
    window._sync_importer = RuneLiteSyncImporter(root)


def _offer(item_id: int, state: str = "BUYING") -> dict[str, object]:
    return {
        "slot": 0,
        "itemId": item_id,
        "itemName": "Item",
        "offerPrice": 210,
        "totalQuantity": 2_976,
        "quantityFilled": 0,
        "spentGp": 0,
        "state": state,
    }


def _status_cells(window: MainWindow) -> dict[str, str]:
    return {
        window.journal_table.item(row, 2).text(): window.journal_table.item(row, 1).text()
        for row in range(window.journal_table.rowCount())
    }


def test_a_plan_whose_offer_is_gone_reads_as_planned(window: MainWindow, tmp_path: Path) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    window._journal.track(12_404, "Feldip hills teleport", 302, 650, 756)
    _connect(window, tmp_path, {"0": _offer(12_404)})

    window._render_journal()

    assert _status_cells(window) == {
        "Yellow fin": "● Planned",
        "Feldip hills teleport": "● Pending buy",
    }


def test_a_plan_reads_as_planned_with_the_grand_exchange_empty(
    window: MainWindow, tmp_path: Path
) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {})

    window._render_journal()

    assert _status_cells(window) == {"Yellow fin": "● Planned"}


def test_unreadable_slot_state_relabels_nothing(window: MainWindow, tmp_path: Path) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {})
    (tmp_path / "sync" / "state" / "abc123.json").write_text("{not json", encoding="utf-8")

    window._render_journal()

    assert _status_cells(window) == {"Yellow fin": "● Pending buy"}


def test_without_runelite_no_plan_is_relabelled(window: MainWindow, tmp_path: Path) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)

    window._render_journal()

    assert _status_cells(window) == {"Yellow fin": "● Pending buy"}


def test_a_planned_row_still_shows_under_the_pending_buy_filter(
    window: MainWindow, tmp_path: Path
) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {"0": _offer(12_404)})
    window.journal_status_filter.setCurrentText("Pending buy")

    window._render_journal()

    assert _status_cells(window) == {"Yellow fin": "● Planned"}


def test_a_part_bought_position_keeps_reading_as_pending(
    window: MainWindow, tmp_path: Path
) -> None:
    position_id = window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    window._journal.update_tracked(position_id, "Pending buy", None, None, None, [(500, 210)])
    _connect(window, tmp_path, {"0": _offer(12_404)})

    window._render_journal()

    assert _status_cells(window) == {"Yellow fin": "● Pending buy"}


class _AnsweredYes:
    StandardButton = QMessageBox.StandardButton

    def __init__(self) -> None:
        self.informed: list[str] = []

    def question(self, *_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    def information(self, _parent: object, title: str, _text: str) -> None:
        self.informed.append(title)

    def warning(self, _parent: object, title: str, text: str) -> None:
        self.informed.append(f"{title}: {text}")


def _select_only_row(window: MainWindow) -> None:
    assert window.journal_table.rowCount() == 1
    window.journal_table.setCurrentCell(0, 0)


def test_a_planned_row_can_be_deleted(window: MainWindow, tmp_path: Path, monkeypatch) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {"0": _offer(12_404)})
    window._render_journal()
    _select_only_row(window)
    message_box = _AnsweredYes()
    monkeypatch.setattr("osrs_toolkit.app.QMessageBox", message_box)

    window._delete_selected_trade()

    assert window._journal.list_tracked() == []
    assert window.journal_table.rowCount() == 0
    assert message_box.informed == []


def test_a_planned_row_can_still_be_cancelled(
    window: MainWindow, tmp_path: Path, monkeypatch
) -> None:
    position_id = window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {"0": _offer(12_404)})
    window._render_journal()
    _select_only_row(window)
    message_box = _AnsweredYes()
    monkeypatch.setattr("osrs_toolkit.app.QMessageBox", message_box)
    monkeypatch.setattr("osrs_toolkit.app.UpdateTrackedTradeDialog", _CancellingDialog)

    window._update_selected_trade()

    assert message_box.informed == []
    assert window._journal.list_tracked()[0].status == "Cancelled"
    assert window._journal.list_tracked()[0].position_id == position_id


def test_the_status_cell_carries_the_stored_status_not_the_label(
    window: MainWindow, tmp_path: Path
) -> None:
    window._journal.track(32_360, "Yellow fin", 2_976, 210, 230)
    _connect(window, tmp_path, {"0": _offer(12_404)})

    window._render_journal()

    status_cell = window.journal_table.item(0, 1)
    assert status_cell.text() == "● Planned"
    assert status_cell.data(Qt.ItemDataRole.UserRole) == "Pending buy"
