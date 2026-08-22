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


def test_an_existing_database_without_the_goal_columns_gets_them_added(tmp_path: Path):
    """The laptop's real accounts.db predates the savings-goal columns and holds one real
    login and pairing token. Opening it again must add the new columns in place rather than
    recreating the table, which would lose both."""
    import sqlite3

    path = tmp_path / "accounts.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE account (
            discord_user_id TEXT PRIMARY KEY,
            username        TEXT NOT NULL,
            avatar          TEXT,
            pairing_token   TEXT,
            created_at      TEXT NOT NULL,
            last_login_at   TEXT NOT NULL
        );
        INSERT INTO account VALUES ('111', 'Wolklaw', NULL, 'real-token', 'then', 'then');
        """
    )
    connection.commit()
    connection.close()

    db = AccountsDB(path)

    account = db.get("111")
    assert account.pairing_token == "real-token"
    assert account.goal_target is None

    db.set_savings_goal("111", "New bank", 50_000_000)
    assert db.get("111").goal_target == 50_000_000
