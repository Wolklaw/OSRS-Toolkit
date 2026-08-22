from __future__ import annotations

from pathlib import Path

from toolkit_web.accounts_db import AccountsDB


def test_a_first_login_creates_the_account(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")

    account = db.upsert_login("111", "Wolklaw", "https://cdn.discordapp.com/a.png")

    assert account.discord_user_id == "111"
    assert account.username == "Wolklaw"
    assert account.paired is False


def test_an_account_starts_unpaired(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)

    assert db.get("111").paired is False


def test_a_later_login_refreshes_the_display_name_rather_than_creating_a_second_account(
    tmp_path: Path,
):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "OldName", None)

    db.upsert_login("111", "NewName", "https://cdn.discordapp.com/new.png")

    account = db.get("111")
    assert account.username == "NewName"
    assert account.avatar == "https://cdn.discordapp.com/new.png"


def test_a_relogin_does_not_disturb_an_already_set_pairing_token(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)
    db.set_pairing_token("111", "abc-token")

    db.upsert_login("111", "Wolklaw", None)

    assert db.get("111").pairing_token == "abc-token"
    assert db.get("111").paired is True


def test_clearing_the_pairing_token(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)
    db.set_pairing_token("111", "abc-token")

    db.set_pairing_token("111", None)

    assert db.get("111").pairing_token is None
    assert db.get("111").paired is False


def test_an_unknown_account_reads_as_none(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")

    assert db.get("does-not-exist") is None
