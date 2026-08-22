from __future__ import annotations

from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.supplies_report import supplies_spend_rows, total_supplies_spend


def test_supplies_spend_rows_ignores_non_supplies_positions(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    repository.track(1234, "Dragon bones", 100, 2_000, 2_500)

    rows = supplies_spend_rows(repository.list_tracked())

    assert rows == []


def test_supplies_spend_rows_groups_by_item(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    first = repository.track(379, "Lobster", 5_000, 150, 150)
    repository.update_tracked(first, "Supplies", None, None, None, [(5_000, 150)])
    second = repository.track(379, "Lobster", 2_000, 160, 160)
    repository.update_tracked(second, "Supplies", None, None, None, [(2_000, 160)])

    (row,) = supplies_spend_rows(repository.list_tracked())

    assert row.item_name == "Lobster"
    assert row.quantity == 7_000
    assert row.spent == 5_000 * 150 + 2_000 * 160
    assert row.purchases == 2


def test_supplies_spend_rows_sorts_by_spend_descending(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    cheap = repository.track(1, "Cheap item", 10, 5, 5)
    repository.update_tracked(cheap, "Supplies", None, None, None, [(10, 5)])
    expensive = repository.track(2, "Expensive item", 10, 5_000, 5_000)
    repository.update_tracked(expensive, "Supplies", None, None, None, [(10, 5_000)])

    rows = supplies_spend_rows(repository.list_tracked())

    assert [row.item_name for row in rows] == ["Expensive item", "Cheap item"]


def test_supplies_spend_rows_uses_the_most_recent_purchase_date(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    first = repository.track(379, "Lobster", 100, 150, 150)
    repository.update_tracked(first, "Supplies", None, None, None, [(100, 150)])
    with repository._connect() as connection:
        connection.execute(
            "UPDATE tracked_trades SET created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE position_id = ?",
            (first,),
        )
    second = repository.track(379, "Lobster", 100, 150, 150)
    repository.update_tracked(second, "Supplies", None, None, None, [(100, 150)])
    with repository._connect() as connection:
        connection.execute(
            "UPDATE tracked_trades SET created_at = '2026-06-01T00:00:00+00:00' "
            "WHERE position_id = ?",
            (second,),
        )

    (row,) = supplies_spend_rows(repository.list_tracked())

    assert row.last_bought == "2026-06-01T00:00:00+00:00"


def test_total_supplies_spend_ignores_flips(tmp_path: Path) -> None:
    repository = JournalRepository(tmp_path / "journal.db")
    flip_id = repository.track(1234, "Dragon bones", 100, 2_000, 2_500)
    repository.update_tracked(flip_id, "Completed", None, None, [(100, 2_500)], [(100, 2_000)])
    supplies_id = repository.track(379, "Lobster", 100, 150, 150)
    repository.update_tracked(supplies_id, "Supplies", None, None, None, [(100, 150)])

    assert total_supplies_spend(repository.list_tracked()) == 100 * 150
