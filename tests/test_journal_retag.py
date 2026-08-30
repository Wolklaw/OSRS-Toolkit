"""Refiling a batch of positions at once.

The case this exists for: a long shopping trip lands twenty positions and every one of them
is filed wrong. What matters most here is what a batch edit must *not* do — the fills and
averages behind each position are the real numbers, and they differ per row, so nothing in a
batch write may go near them.
"""

from __future__ import annotations

import pytest

from osrs_toolkit.journal import UNCHANGED, JournalRepository


@pytest.fixture
def repository(tmp_path) -> JournalRepository:
    return JournalRepository(tmp_path / "journal.db")


def _bought(repository: JournalRepository, name: str, quantity: int = 100) -> int:
    """A position that has actually bought its full quantity at 950."""
    position_id = repository.track(1, name, quantity, target_buy=1_000, target_sell=1_200)
    repository.update_tracked(position_id, "Bought", None, None, None, [(quantity, 950)])
    return position_id


def _by_id(repository: JournalRepository, position_id: int):
    return next(t for t in repository.list_tracked() if t.position_id == position_id)


# -- what it must not touch ----------------------------------------------------------------


def test_refiling_leaves_every_recorded_number_alone(repository: JournalRepository):
    """The whole reason this does not route through ``update_tracked``: that one recomputes
    the averages from the fills it is handed and writes the result unconditionally, so a
    caller with no fills -- which is what "just change the status" is -- blanks them."""
    ids = [_bought(repository, name) for name in ("Shark", "Karambwan")]

    repository.retag_positions(ids, status="Supplies", strategy="Patient (4h+)")

    for position_id in ids:
        trade = _by_id(repository, position_id)
        assert trade.actual_buy == 950
        assert trade.bought_quantity == 100
        assert trade.quantity == 100
        assert [(f.quantity, f.buy_price) for f in trade.buy_fills] == [(100, 950)]


def test_only_the_fields_asked_for_move(repository: JournalRepository):
    position_id = _bought(repository, "Shark")
    before = _by_id(repository, position_id)

    repository.retag_positions([position_id], strategy="Patient (4h+)")

    after = _by_id(repository, position_id)
    assert after.strategy == "Patient (4h+)"
    assert after.status == before.status
    assert after.account_hash == before.account_hash


def test_asking_for_nothing_writes_nothing(repository: JournalRepository):
    position_id = _bought(repository, "Shark")

    assert repository.retag_positions([position_id]).updated == 0
    assert repository.retag_positions([], status="Supplies").updated == 0


# -- the shopping-trip case ----------------------------------------------------------------


def test_a_whole_shopping_trip_becomes_supplies_in_one_go(repository: JournalRepository):
    ids = [_bought(repository, name) for name in ("Shark", "Karambwan", "Prayer potion(4)")]

    result = repository.retag_positions(ids, status="Supplies")

    assert result.updated == 3
    assert result.skipped == ()
    assert {t.status for t in repository.list_tracked()} == {"Supplies"}


def test_the_same_position_named_twice_is_written_once(repository: JournalRepository):
    position_id = _bought(repository, "Shark")

    result = repository.retag_positions([position_id, position_id], status="Supplies")

    assert result.updated == 1


def test_a_position_that_is_not_there_is_ignored(repository: JournalRepository):
    position_id = _bought(repository, "Shark")

    result = repository.retag_positions([position_id, 9999], status="Supplies")

    assert result.updated == 1


# -- the one status with an invariant ------------------------------------------------------


def test_completed_is_refused_for_a_position_its_fills_do_not_cover(
    repository: JournalRepository,
):
    """The same bar ``update_tracked`` holds a single position to. Writing it anyway would
    put rows into a state the single-row editor would have refused to save."""
    part_bought = _bought(repository, "Shark")  # bought, never sold

    result = repository.retag_positions([part_bought], status="Completed")

    assert result.updated == 0
    assert result.skipped == (part_bought,)
    assert _by_id(repository, part_bought).status == "Bought"


def test_completed_is_allowed_where_both_sides_are_covered(repository: JournalRepository):
    position_id = repository.track(2, "Whip", 1, target_buy=1_000, target_sell=1_200)
    repository.update_tracked(position_id, "Bought", None, None, [(1, 1_200)], [(1, 1_000)])

    result = repository.retag_positions([position_id], status="Completed")

    assert result.updated == 1
    trade = _by_id(repository, position_id)
    assert trade.status == "Completed"
    assert trade.completed_at is not None


def test_a_mixed_batch_files_what_it_can_and_reports_the_rest(repository: JournalRepository):
    part_bought = _bought(repository, "Shark")
    finished = repository.track(2, "Whip", 1, target_buy=1_000, target_sell=1_200)
    repository.update_tracked(finished, "Bought", None, None, [(1, 1_200)], [(1, 1_000)])

    result = repository.retag_positions([part_bought, finished], status="Completed")

    assert result.updated == 1
    assert result.skipped == (part_bought,)


def test_an_unknown_status_is_refused_rather_than_written(repository: JournalRepository):
    position_id = _bought(repository, "Shark")

    with pytest.raises(ValueError):
        repository.retag_positions([position_id], status="Sold-ish")

    assert _by_id(repository, position_id).status == "Bought"


# -- character attribution, where None is a real answer -------------------------------------


def test_a_character_can_be_set_and_explicitly_cleared(repository: JournalRepository):
    position_id = _bought(repository, "Shark")

    repository.retag_positions([position_id], account_hash="abc123")
    assert _by_id(repository, position_id).account_hash == "abc123"

    repository.retag_positions([position_id], account_hash=None)
    assert _by_id(repository, position_id).account_hash is None


def test_leaving_the_character_out_does_not_clear_it(repository: JournalRepository):
    """``None`` means "no character in particular", so it cannot double as "leave alone" --
    without the sentinel, changing only the status would unfile every position."""
    position_id = _bought(repository, "Shark")
    repository.retag_positions([position_id], account_hash="abc123")

    repository.retag_positions([position_id], status="Supplies")

    assert _by_id(repository, position_id).account_hash == "abc123"
    assert repository.retag_positions([position_id], account_hash=UNCHANGED).updated == 0


# -- the website has to hear about it -------------------------------------------------------


def test_a_refiled_position_is_offered_to_the_mirror(repository: JournalRepository):
    """Rows sync by ``updated_at``. A batch edit that did not move it would apply here and
    silently never reach the website or another machine."""
    position_id = _bought(repository, "Shark")
    # Exported rows are keyed by sync_uid, not by the local autoincrement id, so the item
    # name is what identifies this one on the way out.
    before = repository.sync_export()["tracked_trades"]
    stamp_before = next(row["updated_at"] for row in before if row["item_name"] == "Shark")

    repository.retag_positions([position_id], status="Supplies")

    after = repository.sync_export()["tracked_trades"]
    row = next(row for row in after if row["item_name"] == "Shark")
    assert row["updated_at"] > stamp_before
    assert row["status"] == "Supplies"
