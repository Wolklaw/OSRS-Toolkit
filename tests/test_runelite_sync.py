from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

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


def test_imports_ge_fill_and_removes_event_file(tmp_path: Path) -> None:
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
    trade = repository.list_synced_trades()[0]
    assert trade.source == "Grand Exchange"
    assert trade.direction == "buy"
    assert trade.given_value == 50_000
    assert trade.received[0].item_name == "Coal"


def test_ge_fill_import_auto_applies_to_a_tracked_position(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(453, "Coal", 500, 100, 120)

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

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    assert result.applied_to_tracked == 1
    position = next(t for t in repository.list_tracked() if t.position_id == position_id)
    assert position.status == "Bought"
    assert position.bought_quantity == 500
    assert position.average_buy_price == 100


def test_ge_fill_import_with_no_matching_position_and_no_total_quantity_is_left_unmatched(
    tmp_path: Path,
) -> None:
    """Without the offer's total_quantity, a fresh fill can't be sized correctly — even
    though this particular fill happens to be the whole order (offer_state: BOUGHT), the
    app has no way to tell that apart from just one slice of a much bigger order without
    total_quantity, so it is left for the RuneLite activity feed rather than guessed at."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
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

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    assert result.applied_to_tracked == 0
    assert repository.list_tracked() == []


def test_ge_fill_import_sizes_new_position_to_the_offers_total_quantity(tmp_path: Path) -> None:
    """A partially filled offer reports its own total_quantity; the auto-created position
    should use that as its target so the offer's remaining fills keep landing on the same
    position instead of each spawning their own."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _event(
        "ge_fill",
        {
            "offer_id": "offer-1",
            "offer_slot": 2,
            "offer_price": 60,
            "offer_state": "BUYING",
            "side": "buy",
            "item_id": 24_615,
            "item_name": "Blighted teleport spell sack",
            "quantity": 77,
            "coins": 4_531,
            "total_quantity": 8_000,
        },
    )
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 1
    assert result.applied_to_tracked == 1
    created = repository.list_tracked()[0]
    assert created.quantity == 8_000
    assert created.status == "Pending buy"
    assert created.bought_quantity == 77


def test_ge_fill_import_seeds_the_live_suggestion_for_an_untracked_position(
    tmp_path: Path,
) -> None:
    """The price lookup passed to import_pending reaches a freshly created position's sell
    suggestion, so a fill nobody pre-tracked still gets a real profit estimate."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _event(
        "ge_fill",
        {
            "offer_id": "offer-1",
            "offer_slot": 2,
            "offer_price": 60,
            "offer_state": "BOUGHT",
            "side": "buy",
            "item_id": 24_615,
            "item_name": "Blighted teleport spell sack",
            "quantity": 8_000,
            "coins": 480_000,
            "total_quantity": 8_000,
        },
    )
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository, {24_615: 68})

    assert result.applied_to_tracked == 1
    created = repository.list_tracked()[0]
    assert created.target_sell == 60
    assert created.sell_suggestion == 68


def test_offer_opened_import_seeds_the_live_suggestion_for_an_untracked_position(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _event(
        "ge_offer_opened",
        {
            "offer_id": "offer-1",
            "offer_slot": 2,
            "offer_price": 1_500,
            "side": "buy",
            "item_id": 4_151,
            "item_name": "Whip",
            "total_quantity": 10,
        },
    )
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository, {4_151: 1_650})

    assert result.applied_to_tracked == 1
    created = repository.list_tracked()[0]
    assert created.target_sell == 1_500
    assert created.sell_suggestion == 1_650


def test_a_position_manually_marked_supplies_still_absorbs_its_remaining_fills(
    tmp_path: Path,
) -> None:
    """Regression: reclassifying a still-filling position to 'Supplies' must not stop the
    matcher from seeing it, or the offer's next fill would spawn a duplicate row."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(379, "Lobster", 5_000, 150, 150)
    repository.update_tracked(position_id, "Supplies", None, None, None, [(2_000, 150)])
    event = _event(
        "ge_fill",
        {
            "offer_id": "offer-1",
            "offer_slot": 2,
            "offer_price": 150,
            "offer_state": "BOUGHT",
            "side": "buy",
            "item_id": 379,
            "item_name": "Lobster",
            "quantity": 3_000,
            "coins": 450_000,
            "total_quantity": 5_000,
        },
    )
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.applied_to_tracked == 1
    trades = repository.list_tracked()
    assert len(trades) == 1
    assert trades[0].position_id == position_id
    assert trades[0].status == "Supplies"
    assert trades[0].bought_quantity == 5_000


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


def test_locked_event_file_left_for_retry_but_still_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup failure (e.g. a locked file) must not lose the durable DB commit or crash."""
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
    event_file = f"{event['event_id']}.json"

    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == event_file:
            raise OSError("locked")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    first = RuneLiteSyncImporter(root).import_pending(repository)

    assert first.imported == 1
    assert first.rejected == 0
    assert len(list((root / "events").glob("*.json"))) == 1

    monkeypatch.setattr(Path, "unlink", original_unlink)
    second = RuneLiteSyncImporter(root).import_pending(repository)
    assert second.duplicates == 1
    assert len(list((root / "events").glob("*.json"))) == 0


def test_imports_loadout_snapshot_and_replaces_earlier_one(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    importer = RuneLiteSyncImporter(root)

    first = _event(
        "loadout_snapshot",
        {
            "equipment": [{"item_id": 1, "item_name": "Rune scimitar", "quantity": 1, "unit_value": 15_000}],
            "inventory": [{"item_id": 995, "item_name": "Coins", "quantity": 100_000, "unit_value": 1}],
            "bank": [{"item_id": 11_802, "item_name": "Armadyl godsword", "quantity": 1, "unit_value": 40_000_000}],
            "skills": {"Attack": 75, "Strength": 80},
        },
    )
    _write_event(root, first)
    result = importer.import_pending(repository)
    assert result.imported == 1

    snapshot = repository.get_loadout_snapshot("123456789")
    assert snapshot is not None
    assert snapshot.skills["Attack"] == 75
    assert snapshot.quantity_owned(11_802) == 1
    assert 995 in snapshot.owned_item_ids

    second = _event(
        "loadout_snapshot",
        {
            "equipment": [],
            "inventory": [],
            "bank": [{"item_id": 11_802, "item_name": "Armadyl godsword", "quantity": 2, "unit_value": 40_000_000}],
            "skills": {"Attack": 76, "Strength": 80},
        },
    )
    second["event_id"] = "3d6c8b8a-6b0a-4a9a-9a3f-7f9a5e6c8b2a"
    _write_event(root, second)
    importer.import_pending(repository)

    updated = repository.get_loadout_snapshot("123456789")
    assert updated is not None
    assert updated.skills["Attack"] == 76
    assert updated.quantity_owned(11_802) == 2
    assert 995 not in updated.owned_item_ids


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


def _offer_event(
    event_type: str,
    event_id: str,
    occurred_at: str,
    payload: dict[str, object],
) -> dict[str, object]:
    event = _event(event_type, payload)
    event["event_id"] = event_id
    event["occurred_at"] = occurred_at
    return event


_COAL_OFFER: dict[str, object] = {
    "offer_id": "offer-1",
    "offer_slot": 2,
    "offer_price": 100,
    "side": "buy",
    "item_id": 453,
    "item_name": "Coal",
    "total_quantity": 500,
}


def test_an_offer_event_left_in_the_queue_is_not_applied_a_second_time(
    tmp_path: Path,
) -> None:
    """Regression: an opening edits a position but records nothing in the activity log, so
    unlike a fill it had nothing to be recognised by. A queue file that outlives its import —
    a locked file on Windows — was applied again on the next pass, opening a second row for
    an offer already tracked."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _offer_event(
        "ge_offer_opened",
        "aaaaaaaa-0000-4000-8000-000000000001",
        "2026-08-11T05:00:00Z",
        dict(_COAL_OFFER),
    )
    _write_event(root, event)
    importer = RuneLiteSyncImporter(root)

    first = importer.import_pending(repository)
    # Stands in for a delete that failed: the same file is back in front of the next pass.
    _write_event(root, event)
    second = importer.import_pending(repository)

    assert (first.imported, first.duplicates) == (1, 0)
    assert (second.imported, second.duplicates) == (0, 1)
    assert len(repository.list_tracked()) == 1


def test_a_restored_offer_rejoins_the_position_already_tracking_it(tmp_path: Path) -> None:
    """The game re-sends every live offer on login and on a world hop. The plugin marks those,
    and a marked one belongs to whatever is already tracking it — however much has bought
    against it, and whatever price the plan was made at."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(453, "Coal", 500, 110, 130)
    repository.update_tracked(position_id, "Pending buy", None, None, None, [(100, 100)])
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:00:00Z",
            {**_COAL_OFFER, "restored": True},
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.applied_to_tracked == 1
    positions = repository.list_tracked()
    assert len(positions) == 1
    assert positions[0].position_id == position_id
    assert positions[0].bought_quantity == 100


def test_offer_is_opened_before_its_fills_even_when_file_names_disagree(tmp_path: Path) -> None:
    """The queue is named by random UUID, so file order says nothing about event order. Applying
    a fill before the opening that created its position would leave one offer split across two
    Journal rows — one from the fill, one from the opening that followed it."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    # Named so sorting by file name puts the fill first, while time puts the opening first.
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "ffffffff-0000-4000-8000-000000000001",
            "2026-08-11T05:00:00Z",
            dict(_COAL_OFFER),
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_fill",
            "00000000-0000-4000-8000-000000000002",
            "2026-08-11T05:00:05Z",
            {**_COAL_OFFER, "offer_state": "BUYING", "quantity": 100, "coins": 10_000},
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 2
    positions = repository.list_tracked()
    assert len(positions) == 1
    assert positions[0].quantity == 500
    assert positions[0].bought_quantity == 100
    assert positions[0].status == "Pending buy"


def test_cancelling_a_buy_that_never_filled_removes_the_position_it_opened(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:00:00Z",
            dict(_COAL_OFFER),
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "bbbbbbbb-0000-4000-8000-000000000002",
            "2026-08-11T05:01:00Z",
            dict(_COAL_OFFER),
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.imported == 2
    assert result.applied_to_tracked == 2
    assert repository.list_tracked() == []


def test_cancelling_a_part_filled_buy_resizes_the_position_to_what_bought(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:00:00Z",
            dict(_COAL_OFFER),
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_fill",
            "bbbbbbbb-0000-4000-8000-000000000002",
            "2026-08-11T05:00:30Z",
            {**_COAL_OFFER, "offer_state": "BUYING", "quantity": 120, "coins": 12_000},
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "cccccccc-0000-4000-8000-000000000003",
            "2026-08-11T05:01:00Z",
            dict(_COAL_OFFER),
        ),
    )

    RuneLiteSyncImporter(root).import_pending(repository)

    position = repository.list_tracked()[0]
    assert position.status == "Bought"
    assert position.quantity == 120
    assert position.bought_quantity == 120
    assert position.average_buy_price == 100


def test_a_final_fill_still_lands_when_it_shares_a_timestamp_with_the_cancellation(
    tmp_path: Path,
) -> None:
    """The plugin writes the last fill and the cancellation back to back, so they can carry the
    same instant. Applying the cancellation first would resize the position before the fill
    arrived, and the fill would then have nowhere left to go."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:00:00Z",
            dict(_COAL_OFFER),
        ),
    )
    # Sorts before the fill by file name, and ties with it on time.
    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "00000000-0000-4000-8000-000000000002",
            "2026-08-11T05:01:00Z",
            dict(_COAL_OFFER),
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_fill",
            "eeeeeeee-0000-4000-8000-000000000003",
            "2026-08-11T05:01:00Z",
            {**_COAL_OFFER, "offer_state": "CANCELLED_BUY", "quantity": 200, "coins": 20_000},
        ),
    )

    RuneLiteSyncImporter(root).import_pending(repository)

    position = repository.list_tracked()[0]
    assert position.status == "Bought"
    assert position.quantity == 200
    assert position.bought_quantity == 200


def test_cancelling_a_sell_returns_the_position_to_bought(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(453, "Coal", 500, 100, 120)
    repository.update_tracked(position_id, "Listed for sale", 100, None)

    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:01:00Z",
            {**_COAL_OFFER, "side": "sell", "offer_price": 120},
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.applied_to_tracked == 1
    position = repository.list_tracked()[0]
    assert position.status == "Bought"
    assert position.quantity == 500


def test_cancelling_a_partial_sell_listing_still_returns_the_position_to_bought(
    tmp_path: Path,
) -> None:
    """A sell offer can cover part of a position, so its size says nothing about the position's.
    Only the listing status is reliable to match on — and putting it back is harmless if wrong."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    position_id = repository.track(453, "Coal", 500, 100, 120)
    repository.update_tracked(position_id, "Listed for sale", 100, None)

    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:01:00Z",
            {**_COAL_OFFER, "side": "sell", "offer_price": 120, "total_quantity": 300},
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.applied_to_tracked == 1
    position = repository.list_tracked()[0]
    assert position.status == "Bought"
    assert position.quantity == 500


def test_cancelling_leaves_a_hand_made_position_of_a_different_size_alone(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    repository.track(453, "Coal", 900, 100, 120)

    _write_event(
        root,
        _offer_event(
            "ge_offer_cancelled",
            "aaaaaaaa-0000-4000-8000-000000000001",
            "2026-08-11T05:01:00Z",
            dict(_COAL_OFFER),
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.applied_to_tracked == 0
    position = repository.list_tracked()[0]
    assert position.quantity == 900
    assert position.status == "Pending buy"


def test_events_replay_in_time_order_when_the_writer_omitted_zero_seconds(
    tmp_path: Path,
) -> None:
    """Java's instant formatter drops fields that are zero, so "05:00Z" and "05:00:30Z" sort the
    wrong way round as text. Only comparing them as instants gets the order right."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "ffffffff-0000-4000-8000-000000000001",
            "2026-08-11T05:00Z",
            dict(_COAL_OFFER),
        ),
    )
    _write_event(
        root,
        _offer_event(
            "ge_fill",
            "00000000-0000-4000-8000-000000000002",
            "2026-08-11T05:00:30Z",
            {**_COAL_OFFER, "offer_state": "BUYING", "quantity": 100, "coins": 10_000},
        ),
    )

    RuneLiteSyncImporter(root).import_pending(repository)

    positions = repository.list_tracked()
    assert len(positions) == 1
    assert positions[0].bought_quantity == 100


def test_an_event_type_this_build_does_not_know_waits_instead_of_being_quarantined(
    tmp_path: Path,
) -> None:
    """The plugin updates itself through the Plugin Hub while the app is updated by hand, so a
    plugin ahead of the app is normal. Quarantining its events would move them out of the queue
    before the version that understands them ever arrives."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _offer_event(
        "ge_offer_amended",
        "aaaaaaaa-0000-4000-8000-000000000001",
        "2026-08-11T05:00:00Z",
        dict(_COAL_OFFER),
    )
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.skipped == 1
    assert result.rejected == 0
    assert (root / "events" / f"{event['event_id']}.json").exists()
    assert not (root / "rejected").exists()


def test_a_newer_schema_version_also_waits_rather_than_being_quarantined(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _offer_event(
        "ge_offer_opened",
        "aaaaaaaa-0000-4000-8000-000000000001",
        "2026-08-11T05:00:00Z",
        dict(_COAL_OFFER),
    )
    event["schema_version"] = 2
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.skipped == 1
    assert result.rejected == 0
    assert (root / "events" / f"{event['event_id']}.json").exists()


def test_a_genuinely_malformed_event_is_still_quarantined(tmp_path: Path) -> None:
    """Waiting is only right for events that are sound but unfamiliar. A file with a schema
    version that isn't a number is broken, and no future version will rescue it."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    event = _offer_event(
        "ge_offer_opened",
        "aaaaaaaa-0000-4000-8000-000000000001",
        "2026-08-11T05:00:00Z",
        dict(_COAL_OFFER),
    )
    event["schema_version"] = "not a number"
    _write_event(root, event)

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.rejected == 1
    assert result.skipped == 0
    assert not (root / "events" / f"{event['event_id']}.json").exists()
    assert len(list((root / "rejected").glob("*.invalid"))) == 1


def test_a_backlog_of_unknown_events_does_not_starve_the_ones_that_work(
    tmp_path: Path,
) -> None:
    """Unknown events stay queued, so without stepping over them a large backlog would fill the
    per-pass budget and the app would stop importing anything it does understand."""
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    for index in range(600):
        _write_event(
            root,
            _offer_event(
                "ge_offer_amended",
                f"00000000-0000-4000-8000-{index:012d}",
                "2026-08-11T05:00:00Z",
                dict(_COAL_OFFER),
            ),
        )
    # Sorts last, so a budget spent on the backlog would never reach it.
    _write_event(
        root,
        _offer_event(
            "ge_offer_opened",
            "ffffffff-0000-4000-8000-000000000001",
            "2026-08-11T05:00:10Z",
            dict(_COAL_OFFER),
        ),
    )

    result = RuneLiteSyncImporter(root).import_pending(repository)

    assert result.skipped == 600
    assert result.imported == 1
    assert len(repository.list_tracked()) == 1


def test_rejected_events_are_capped_so_they_cannot_fill_the_disk(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    repository = JournalRepository(tmp_path / "journal.db")
    rejected = root / "rejected"
    rejected.mkdir(parents=True)
    for index in range(210):
        path = rejected / f"old-{index:04d}.invalid"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    (root / "events").mkdir(parents=True, exist_ok=True)

    RuneLiteSyncImporter(root).import_pending(repository)

    remaining = sorted(path.name for path in rejected.glob("*.invalid"))
    assert len(remaining) == 200
    # The oldest went first.
    assert remaining[0] == "old-0010.invalid"


def _write_offer_state(root: Path, account_hash: str, slots: dict) -> None:
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{account_hash}.json").write_text(json.dumps(slots), encoding="utf-8")


def _slot_payload(
    slot: int,
    item_id: int = 4_151,
    item_name: str = "Whip",
    offer_price: int = 1_500,
    total_quantity: int = 10,
    quantity_filled: int = 4,
    spent_gp: int = 6_000,
    state: str = "BUYING",
) -> dict:
    return {
        "slot": slot,
        "itemId": item_id,
        "itemName": item_name,
        "offerPrice": offer_price,
        "totalQuantity": total_quantity,
        "quantityFilled": quantity_filled,
        "spentGp": spent_gp,
        "state": state,
        "offerId": "some-uuid",
    }


def test_read_offer_state_reflects_a_slot_never_filled_at_all(tmp_path: Path) -> None:
    """The one thing no combination of synced events can tell the app on their own: a
    brand-new offer with zero fills yet still shows up, because this reads the plugin's
    own live bookkeeping instead of reconstructing state from history."""
    root = tmp_path / "sync"
    _write_offer_state(
        root, "abc123", {"2": _slot_payload(2, quantity_filled=0, spent_gp=0, state="BUYING")}
    )

    slots = RuneLiteSyncImporter(root).read_offer_state("abc123")

    assert set(slots) == {2}
    assert slots[2].item_name == "Whip"
    assert slots[2].quantity_filled == 0
    assert slots[2].side == "buy"


def test_read_offer_state_derives_side_and_percent_filled(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write_offer_state(
        root,
        "abc123",
        {"5": _slot_payload(5, total_quantity=10, quantity_filled=4, state="SELLING")},
    )

    (slot,) = RuneLiteSyncImporter(root).read_offer_state("abc123").values()

    assert slot.side == "sell"
    assert slot.percent_filled == 40.0
    assert slot.is_terminal is False


def test_read_offer_state_flags_terminal_uncollected_offers(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write_offer_state(root, "abc123", {"0": _slot_payload(0, state="BOUGHT")})

    (slot,) = RuneLiteSyncImporter(root).read_offer_state("abc123").values()

    assert slot.is_terminal is True


def test_read_placed_offers_tells_an_empty_grand_exchange_from_an_unreadable_one(
    tmp_path: Path,
) -> None:
    """Regression: every slot collected is the ordinary way to have no offers, and reading it
    as "cannot say" is what stopped a plan with nothing placed for it ever being labelled."""
    root = tmp_path / "sync"
    _write_offer_state(root, "abc123", {})

    assert RuneLiteSyncImporter(root).read_placed_offers("abc123") == {}


def test_read_placed_offers_is_none_with_no_state_written(tmp_path: Path) -> None:
    assert RuneLiteSyncImporter(tmp_path / "sync").read_placed_offers("abc123") is None


def test_read_placed_offers_is_none_when_the_state_cannot_be_parsed(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    (root / "state").mkdir(parents=True)
    (root / "state" / "abc123.json").write_text("{not json", encoding="utf-8")

    assert RuneLiteSyncImporter(root).read_placed_offers("abc123") is None


def test_read_offer_state_still_reads_an_unreadable_file_as_no_slots(tmp_path: Path) -> None:
    """The dashboard only cares what is in each slot, so nothing there changes."""
    assert RuneLiteSyncImporter(tmp_path / "sync").read_offer_state("abc123") == {}


def test_read_offer_state_is_empty_when_the_file_does_not_exist(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    assert RuneLiteSyncImporter(root).read_offer_state("abc123") == {}


def test_read_offer_state_skips_a_malformed_slot_without_losing_the_others(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sync"
    _write_offer_state(
        root,
        "abc123",
        {
            "0": _slot_payload(0),
            "1": {"itemId": "not a number"},
        },
    )

    slots = RuneLiteSyncImporter(root).read_offer_state("abc123")

    assert set(slots) == {0}


def test_read_offer_state_ignores_an_out_of_range_slot_index(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write_offer_state(root, "abc123", {"99": _slot_payload(99)})

    assert RuneLiteSyncImporter(root).read_offer_state("abc123") == {}


def test_read_offer_state_scopes_by_account_hash(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    _write_offer_state(root, "abc123", {"0": _slot_payload(0)})

    assert RuneLiteSyncImporter(root).read_offer_state("different") == {}


def test_read_offer_state_rejects_an_oversized_file(tmp_path: Path) -> None:
    root = tmp_path / "sync"
    state_dir = root / "state"
    state_dir.mkdir(parents=True)
    huge = {str(i): _slot_payload(0) for i in range(50_000)}
    (state_dir / "abc123.json").write_text(json.dumps(huge), encoding="utf-8")

    assert RuneLiteSyncImporter(root).read_offer_state("abc123") == {}


def test_ge_offer_status_label_reads_naturally() -> None:
    from osrs_toolkit.runelite_sync import ge_offer_status_label

    assert ge_offer_status_label("BUYING") == "Buying"
    assert ge_offer_status_label("BOUGHT") == "Bought — collect"
    assert ge_offer_status_label("CANCELLED_SELL") == "Cancelled — collect"
