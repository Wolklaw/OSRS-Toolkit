from __future__ import annotations

import json
import os
import time
from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter


def _event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": "9ca987d2-bcd1-45ea-8320-b17a956e38c9",
        "event_type": event_type,
        "occurred_at": "2026-08-11T05:00:00Z",
        "account": {"hash": "123456789", "name": "Example Player"},
        "payload": payload,
    }


def _write_event(root: Path, event: dict[str, object]) -> None:
    events = root / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / f"{event['event_id']}.json").write_text(json.dumps(event), encoding="utf-8")


def test_imports_ge_fill_and_acknowledges(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    event = _event(
        "ge_fill",
        {
            "offer_id": "offer-1",
            "offer_slot": 2,
            "offer_price": 100,
            "offer_state": "BOUGHT",
            "side": "buy",
            "item_id": 453,
            "item_name": "Coal",
            "quantity": 500,
            "coins": 50_000,
        },
    )
    _write_event(root, event)
    repository = JournalRepository(tmp_path / "journal.db")

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    assert not (root / "events" / f"{event['event_id']}.json").exists()
    assert (root / "acks" / f"{event['event_id']}.ack").exists()
    trade = repository.list_synced_trades()[0]
    assert trade.source == "Grand Exchange"
    assert trade.direction == "buy"
    assert trade.given_value == 50_000
    assert trade.received[0].item_name == "Coal"


def test_imports_multi_item_player_trade(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    event = _event(
        "player_trade",
        {
            "counterparty": "Other Player",
            "given": [
                {"item_id": 995, "item_name": "Coins", "quantity": 5_000_000, "unit_value": 1}
            ],
            "received": [
                {"item_id": 2, "item_name": "Cannonball", "quantity": 30_000, "unit_value": 170},
                {"item_id": 453, "item_name": "Coal", "quantity": 2_000, "unit_value": 180},
            ],
        },
    )
    _write_event(root, event)
    repository = JournalRepository(tmp_path / "journal.db")

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    trade = repository.list_synced_trades("player_trade")[0]
    assert trade.counterparty == "Other Player"
    assert len(trade.received) == 2
    assert trade.given_value == 5_000_000
    assert trade.received_value == 5_460_000


def test_rejects_invalid_event_without_losing_it(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    event = _event("ge_fill", {"side": "buy"})
    _write_event(root, event)
    repository = JournalRepository(tmp_path / "journal.db")

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.rejected == 1
    assert repository.list_synced_trades() == []
    assert len(list((root / "rejected").glob("*.invalid"))) == 1


def test_duplicate_event_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    event = _event(
        "ge_fill",
        {
            "side": "sell",
            "item_id": 453,
            "item_name": "Coal",
            "quantity": 10,
            "coins": 2_000,
        },
    )
    repository = JournalRepository(tmp_path / "journal.db")
    importer = RuneLiteSyncImporter(root)
    _write_event(root, event)
    assert importer.import_pending(repository).imported == 1
    _write_event(root, event)
    assert importer.import_pending(repository).duplicates == 1
    assert len(repository.list_synced_trades()) == 1


def test_zero_coin_ge_fill_is_rejected_without_touching_the_journal(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    event = _event(
        "ge_fill",
        {
            "side": "buy",
            "item_id": 453,
            "item_name": "Coal",
            "quantity": 10,
            "coins": 0,
        },
    )
    _write_event(root, event)
    repository = JournalRepository(tmp_path / "journal.db")

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.rejected == 1
    assert repository.list_synced_trades() == []


def test_acknowledgement_failure_leaves_valid_event_for_retry(tmp_path: Path) -> None:
    class FailingAckImporter(RuneLiteSyncImporter):
        def _acknowledge(self, event_id: str) -> None:
            raise OSError(event_id)

    root = tmp_path / "sync"
    event = _event(
        "ge_fill",
        {
            "side": "sell",
            "item_id": 453,
            "item_name": "Coal",
            "quantity": 10,
            "coins": 2_000,
        },
    )
    _write_event(root, event)
    repository = JournalRepository(tmp_path / "journal.db")

    first = FailingAckImporter(root).import_pending(repository)

    assert first.imported == 1
    assert first.rejected == 0
    assert len(list((root / "events").glob("*.json"))) == 1

    second = RuneLiteSyncImporter(root).import_pending(repository)
    assert second.duplicates == 1
    assert len(list((root / "events").glob("*.json"))) == 0


def test_connection_status_exposes_active_runelite_character(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    root.mkdir()
    (root / "status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": True,
                "account_hash": "abc123",
                "account_name": "Example Player",
                "player_trade_tracking": True,
            }
        ),
        encoding="utf-8",
    )

    status = RuneLiteSyncImporter(root).connection_status()

    assert status.active is True
    assert status.account_name == "Example Player"
    assert status.account_hash == "abc123"
    assert status.player_trade_tracking is True


def test_stale_connection_status_keeps_character_but_reports_offline(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    root.mkdir()
    status_path = root / "status.json"
    status_path.write_text(
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
    stale = time.time() - 60
    os.utime(status_path, (stale, stale))

    status = RuneLiteSyncImporter(root).connection_status()

    assert status.active is False
    assert status.account_name == "Example Player"
