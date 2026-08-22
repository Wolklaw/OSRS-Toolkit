"""Drives the real MainWindow._render_ge_offers, the way test_buy_limits_render.py drives
_render_buy_limits. Unlike buy limits, this reads the plugin's own state file rather than
journal data, so window._sync_importer is pointed at a throwaway sync root.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from osrs_toolkit.app import MainWindow
from osrs_toolkit.runelite_sync import GE_SLOT_COUNT, RuneLiteSyncImporter


def _connect(window: MainWindow, tmp_path: Path, account_hash: str = "abc123") -> Path:
    root = tmp_path / "sync"
    root.mkdir()
    (root / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": True,
                "account_hash": account_hash,
                "account_name": "Example Player",
            }
        ),
        encoding="utf-8",
    )
    window._sync_importer = RuneLiteSyncImporter(root)
    return root


def _write_slots(root: Path, slots: dict[str, object], account_hash: str = "abc123") -> None:
    state_dir = root / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / f"{account_hash}.json").write_text(json.dumps(slots), encoding="utf-8")


def _offer(slot: int, state: str = "BUYING", filled: int = 4, total: int = 10) -> dict[str, object]:
    return {
        "slot": slot,
        "itemId": 4_151,
        "itemName": "Whip",
        "offerPrice": 1_500,
        "totalQuantity": total,
        "quantityFilled": filled,
        "spentGp": 6_000,
        "state": state,
        "offerId": "some-uuid",
    }


def test_empty_state_when_runelite_is_not_connected(window: MainWindow) -> None:
    window._render_ge_offers()

    assert window.ge_offers_empty.isHidden() is False
    assert window.ge_slots_frame.isHidden() is True


def test_all_eight_slots_render_even_with_nothing_open(window: MainWindow, tmp_path: Path) -> None:
    _connect(window, tmp_path)

    window._render_ge_offers()

    assert window.ge_offers_empty.isHidden() is True
    assert window.ge_slots_frame.isHidden() is False
    assert len(window.ge_slot_cards) == GE_SLOT_COUNT
    for card in window.ge_slot_cards:
        assert card.property("slotState") == "empty"


def test_an_open_offer_shows_up_in_its_real_slot(window: MainWindow, tmp_path: Path) -> None:
    root = _connect(window, tmp_path)
    _write_slots(root, {"3": _offer(3)})

    window._render_ge_offers()

    # Slot indices from the plugin are 0-based; the dashboard displays them 1-based to
    # match the in-game Grand Exchange window.
    card = window.ge_slot_cards[3]
    assert card.property("slotState") == "buy"
    assert card._number.text() == "4  Buy"
    assert card._item.toolTip() == "Whip"
    assert card._status.text() == "Buying"
    assert card._filled.text() == "4 / 10 · 40%"
    assert card._progress.value() == 40
    # Every other slot is still empty.
    assert window.ge_slot_cards[0].property("slotState") == "empty"


def test_a_finished_offer_reads_as_waiting_to_be_collected(
    window: MainWindow, tmp_path: Path
) -> None:
    """An offer sitting in its slot until collected is the state worth spotting, so it gets
    its own tone rather than reading as one more offer still in progress."""
    root = _connect(window, tmp_path)
    _write_slots(root, {"0": _offer(0, state="BOUGHT", filled=10)})

    window._render_ge_offers()

    card = window.ge_slot_cards[0]
    assert card.property("slotState") == "collect"
    assert card._status.text() == "Bought — collect"
    assert card._progress.value() == 100


def test_a_long_item_name_does_not_stretch_its_slot(
    qt_app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    """Regression: a plain QLabel refuses to shrink below its text, so one long name would
    widen its own column and leave the other seven slots narrower than it."""
    root = _connect(window, tmp_path)
    long_name = "Ancient ceremonial legs of considerable length"
    slot = _offer(2)
    slot["itemName"] = long_name
    _write_slots(root, {"2": slot})
    window._render_ge_offers()

    # Shown and sized, because Qt holds back resize events for hidden widgets and the
    # label only re-elides when it is told how much room it actually got.
    window.nav.setCurrentRow(MainWindow.NAV_ITEMS.index("Trade Journal"))
    window.show()
    window.resize(1100, 800)
    qt_app.processEvents()

    card = window.ge_slot_cards[2]
    assert card._item.toolTip() == long_name, "the full name should survive in the tooltip"
    assert card._item.text() != long_name, "the label should have elided"
    assert card.width() <= window.ge_slot_cards[0].width() + 2, "the long name widened its own slot"
