from __future__ import annotations

from pathlib import Path

from osrs_toolkit.holdings import untracked_holdings
from osrs_toolkit.journal import JournalRepository, LoadoutItem, LoadoutSnapshot
from osrs_toolkit.models import ItemMapping, MarketPoint


def _snapshot(*items: LoadoutItem) -> LoadoutSnapshot:
    return LoadoutSnapshot("h", "Name", "2026-10-25T00:00:00+00:00", (), items, (), {})


def _market() -> tuple[dict[int, ItemMapping], list[MarketPoint]]:
    mappings = {
        2: ItemMapping(2, "Cannonball", True, 11_000, 0),
        995: ItemMapping(995, "Coins", False, None, 0),
    }
    points = [MarketPoint(2, 200, 190, 0, 0, 0, 0)]
    return mappings, points


def test_an_untracked_stack_gets_a_suggested_sell_price_after_tax() -> None:
    mappings, points = _market()
    snapshot = _snapshot(LoadoutItem(2, "Cannonball", 1_000, 195), LoadoutItem(995, "Coins", 5, 1))

    (row,) = untracked_holdings(snapshot, [], mappings, points)

    assert row.item_name == "Cannonball"
    assert row.suggested_sell == 200
    assert row.net_each == 196
    assert row.net_total == 196_000


def test_a_stack_covered_by_a_tracked_purchase_is_not_listed(tmp_path: Path) -> None:
    mappings, points = _market()
    repository = JournalRepository(tmp_path / "journal.db")
    position = repository.track(2, "Cannonball", 1_000, 190, 200)
    repository.update_tracked(position, "Bought", None, None, None, [(1_000, 190)])
    snapshot = _snapshot(LoadoutItem(2, "Cannonball", 1_000, 195))

    assert untracked_holdings(snapshot, repository.list_tracked(), mappings, points) == []


def test_only_the_untracked_remainder_is_listed(tmp_path: Path) -> None:
    mappings, points = _market()
    repository = JournalRepository(tmp_path / "journal.db")
    position = repository.track(2, "Cannonball", 400, 190, 200)
    repository.update_tracked(position, "Bought", None, None, None, [(400, 190)])
    snapshot = _snapshot(LoadoutItem(2, "Cannonball", 1_000, 195))

    (row,) = untracked_holdings(snapshot, repository.list_tracked(), mappings, points)

    assert row.quantity == 600
