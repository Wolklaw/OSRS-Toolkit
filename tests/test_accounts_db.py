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


def test_an_existing_database_without_the_webhook_or_alert_columns_gets_them_added(
    tmp_path: Path,
):
    """Same story as the goal-columns migration above, for the columns price alerts added
    later: a real ``account`` row and a real ``watchlist_item`` row must survive opening an
    older database, with the new columns simply appearing NULL rather than the tables being
    recreated out from under them."""
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
        CREATE TABLE watchlist_item (
            discord_user_id TEXT NOT NULL,
            item_id         INTEGER NOT NULL,
            added_at        TEXT NOT NULL,
            PRIMARY KEY (discord_user_id, item_id)
        );
        INSERT INTO account VALUES ('111', 'Wolklaw', NULL, 'real-token', 'then', 'then');
        INSERT INTO watchlist_item VALUES ('111', 4151, 'then');
        """
    )
    connection.commit()
    connection.close()

    db = AccountsDB(path)

    account = db.get("111")
    assert account.pairing_token == "real-token"
    assert account.discord_webhook_url is None
    [alert] = db.list_watchlist_alerts("111")
    assert alert.item_id == 4151
    assert alert.target_price is None

    db.set_discord_webhook("111", "https://discord.com/api/webhooks/1/abc")
    assert db.set_watchlist_alert("111", 4151, 1_000_000, "above") is True
    assert db.get("111").discord_webhook_url == "https://discord.com/api/webhooks/1/abc"


# -- watchlist alerts --------------------------------------------------------------------------


def test_setting_an_alert_on_an_unwatched_item_fails(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)

    assert db.set_watchlist_alert("111", 4151, 1_000_000, "above") is False


def test_setting_an_alert_arms_it_and_clearing_disarms_it(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)
    db.add_watchlist_item("111", 4151)

    assert db.set_watchlist_alert("111", 4151, 1_000_000, "above") is True
    [alert] = db.list_watchlist_alerts("111")
    assert (alert.target_price, alert.direction) == (1_000_000, "above")

    db.clear_watchlist_alert("111", 4151)
    [alert] = db.list_watchlist_alerts("111")
    assert (alert.target_price, alert.direction) == (None, None)


def test_list_armed_alerts_needs_both_a_webhook_and_an_armed_alert(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)
    db.add_watchlist_item("111", 4151)

    assert db.list_armed_alerts() == []

    db.set_watchlist_alert("111", 4151, 1_000_000, "above")
    assert db.list_armed_alerts() == []  # armed, but no webhook to send to yet

    db.set_discord_webhook("111", "https://discord.com/api/webhooks/1/abc")
    [armed] = db.list_armed_alerts()
    assert armed.item_id == 4151
    assert armed.discord_webhook_url == "https://discord.com/api/webhooks/1/abc"


def test_stamp_alert_sent_records_when_it_last_fired(tmp_path: Path):
    db = AccountsDB(tmp_path / "accounts.db")
    db.upsert_login("111", "Wolklaw", None)
    db.add_watchlist_item("111", 4151)
    db.set_watchlist_alert("111", 4151, 1_000_000, "above")

    db.stamp_alert_sent("111", 4151)

    [alert] = db.list_watchlist_alerts("111")
    assert alert.last_alerted_at is not None
