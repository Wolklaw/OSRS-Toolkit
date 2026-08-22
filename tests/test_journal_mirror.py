"""One pass of the mirror: what it asks, what it sends, and what it remembers.

The merge is tested in ``test_journal_sync_merge`` against two real journals. What matters
here is the conversation around it — above all that an idle pass costs one small question,
because the machine answering it also serves the website.
"""

from __future__ import annotations

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.journal_mirror import JournalMirror
from osrs_toolkit.web_source import ToolkitWebClient


class FakeWebsite:
    """The far side, backed by a real journal so the merge is the real merge."""

    def __init__(self, repository: JournalRepository) -> None:
        self.repository = repository
        self.configured = True
        self.calls: list[str] = []
        self.offline = False

    def get(self, path: str, **params: str) -> object | None:
        self.calls.append(path)
        if self.offline:
            return None
        if path == "/api/journal/version":
            return self.repository.sync_version()
        if path == "/api/journal/pull":
            return self.repository.sync_export(since=params.get("since") or None)
        return None

    def post(self, path: str, body: object) -> object | None:
        self.calls.append(path)
        if self.offline:
            return None
        if path == "/api/journal/push":
            return {"ok": True, **self.repository.sync_apply(body)}
        return None


def _mirror(
    tmp_path, name: str = "desktop"
) -> tuple[JournalMirror, JournalRepository, FakeWebsite]:
    local = JournalRepository(tmp_path / f"{name}.db")
    remote = JournalRepository(tmp_path / f"{name}-website.db")
    website = FakeWebsite(remote)
    return JournalMirror(local, website), remote, website


# -- the idle case, which is nearly every case ---------------------------------------------


def test_an_idle_pass_asks_one_cheap_question(tmp_path):
    """The whole reason the version route exists. This runs every minute against a laptop
    that is also serving the website, so a quiet pass must not read or serialize a journal."""
    mirror, _remote, website = _mirror(tmp_path)
    mirror.sync()
    website.calls.clear()

    result = mirror.sync()

    assert result.checked_only is True
    assert website.calls == ["/api/journal/version"]
    assert result.describe() == "Up to date"


def test_a_local_change_is_noticed_without_the_website_changing(tmp_path):
    mirror, remote, _website = _mirror(tmp_path)
    mirror.sync()

    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    result = mirror.sync()

    assert result.pushed == 1
    assert [trade.item_name for trade in remote.list_all()] == ["Abyssal whip"]


def test_a_remote_change_arrives(tmp_path):
    mirror, remote, _website = _mirror(tmp_path)
    mirror.sync()

    remote.add("Dragon bones", 500, 2_000, 2_600)
    result = mirror.sync()

    assert result.pulled == 1
    assert [trade.item_name for trade in mirror.repository.list_all()] == ["Dragon bones"]


# -- the first pass ------------------------------------------------------------------------


def test_the_first_pass_seeds_an_empty_website(tmp_path):
    """The real one: this machine holds a month of history and the website holds nothing."""
    mirror, remote, _website = _mirror(tmp_path)
    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    mirror.repository.track(
        item_id=4151, item_name="Abyssal whip", quantity=1, target_buy=1, target_sell=2
    )

    result = mirror.sync()

    assert result.pushed == 2
    assert len(remote.list_all()) == 1
    assert len(remote.list_tracked()) == 1


def test_settling_down_after_the_seed(tmp_path):
    """Having exchanged everything, the next pass must go quiet rather than re-sending."""
    mirror, _remote, website = _mirror(tmp_path)
    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    mirror.sync()
    website.calls.clear()

    result = mirror.sync()

    assert result.checked_only is True
    assert website.calls == ["/api/journal/version"]


def test_both_sides_holding_different_history_end_up_with_both(tmp_path):
    mirror, remote, _website = _mirror(tmp_path)
    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    remote.add("Dragon bones", 500, 2_000, 2_600)

    mirror.sync()

    assert {trade.item_name for trade in mirror.repository.list_all()} == {
        "Abyssal whip",
        "Dragon bones",
    }
    assert {trade.item_name for trade in remote.list_all()} == {"Abyssal whip", "Dragon bones"}


# -- when the website is not there ----------------------------------------------------------


def test_an_unreachable_website_changes_nothing(tmp_path):
    mirror, _remote, website = _mirror(tmp_path)
    website.offline = True

    result = mirror.sync()

    assert result.reached is False
    assert result.describe() == "Could not reach the website"


def test_an_unreachable_pass_leaves_the_watermarks_alone(tmp_path):
    """A gap skipped over is a gap never synced. The next pass must ask what this one did."""
    mirror, remote, website = _mirror(tmp_path)
    mirror.sync()
    remote.add("Dragon bones", 500, 2_000, 2_600)

    website.offline = True
    mirror.sync()
    website.offline = False
    result = mirror.sync()

    assert result.pulled == 1
    assert [trade.item_name for trade in mirror.repository.list_all()] == ["Dragon bones"]


def test_a_push_that_goes_unanswered_is_offered_again(tmp_path):
    """It may well have landed — but deciding it did when it did not loses the row. Offering
    it twice costs nothing, because the far side skips what it already has."""
    mirror, remote, website = _mirror(tmp_path)
    mirror.sync()
    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)

    original_post = website.post
    website.post = lambda path, body: None  # lands nowhere, says nothing
    mirror.sync()
    website.post = original_post

    result = mirror.sync()

    assert result.pushed == 1
    assert [trade.item_name for trade in remote.list_all()] == ["Abyssal whip"]


def test_nothing_configured_does_not_pretend(tmp_path):
    local = JournalRepository(tmp_path / "desktop.db")
    mirror = JournalMirror(local, ToolkitWebClient("https://runescope.app", ""))

    result = mirror.sync()

    assert result.reached is False


# -- deletions and forgetting ---------------------------------------------------------------


def test_a_deletion_here_reaches_the_website(tmp_path):
    mirror, remote, _website = _mirror(tmp_path)
    trade_id = mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    mirror.sync()
    assert len(remote.list_all()) == 1

    mirror.repository.delete(trade_id)
    mirror.sync()

    assert remote.list_all() == []


def test_a_deleted_trade_does_not_come_back_on_later_passes(tmp_path):
    mirror, remote, _website = _mirror(tmp_path)
    trade_id = mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    mirror.sync()
    mirror.repository.delete(trade_id)
    mirror.sync()

    for _ in range(3):
        mirror.sync()

    assert mirror.repository.list_all() == []
    assert remote.list_all() == []


def test_resetting_forgets_what_was_synced(tmp_path):
    """For when the credential starts pointing at a different account: what the old one had
    exchanged says nothing about what the new one holds."""
    mirror, _remote, _website = _mirror(tmp_path)
    mirror.repository.add("Abyssal whip", 1, 1_500_000, 1_650_000)
    mirror.sync()

    mirror.reset()

    assert mirror.remote_version is None
    assert mirror.local_version is None
