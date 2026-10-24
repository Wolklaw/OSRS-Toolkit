from __future__ import annotations

import hashlib
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    discord_user_id    TEXT PRIMARY KEY,
    username            TEXT NOT NULL,
    avatar              TEXT,
    pairing_token       TEXT,
    created_at          TEXT NOT NULL,
    last_login_at       TEXT NOT NULL,
    goal_label          TEXT,
    goal_target         INTEGER,
    goal_created_at     TEXT,
    discord_webhook_url TEXT
);

-- A character someone has named for themselves, for anyone without the plugin yet to have
-- something to attribute a manual trade to. account_hash starts NULL and is here for later —
-- once the plugin reports this same character by hash, that is what lets the two be linked
-- as the same one rather than the manual history starting over under a second name.
CREATE TABLE IF NOT EXISTS character (
    character_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL REFERENCES account(discord_user_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    account_hash    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(discord_user_id, name)
);

-- Which character a manual trade belongs to. Kept here rather than as a column on the trade
-- itself: a trade lives in that account's own journal.db, a file this app shares unchanged
-- with the desktop window, and a character is a concept the desktop window has never needed
-- to know about.
--
-- Keyed by the trade's sync_uid rather than its trade_id. An autoincrement id names a
-- different trade on every machine, so once a trade recorded on the desktop can arrive here,
-- an attribution stored against a local id would follow whichever trade happened to land on
-- that number -- attributing somebody's flip to the wrong character rather than to none.
CREATE TABLE IF NOT EXISTS trade_character (
    discord_user_id TEXT NOT NULL REFERENCES account(discord_user_id) ON DELETE CASCADE,
    trade_sync_uid  TEXT NOT NULL,
    character_id    INTEGER NOT NULL REFERENCES character(character_id) ON DELETE CASCADE,
    PRIMARY KEY (discord_user_id, trade_sync_uid)
);

-- A credential the desktop window signs in with, since it has no browser to hold a session
-- cookie. Deliberately not the pairing token: that one is the plugin's write credential to the
-- sync service, and a plugin token that could also read somebody's whole journal would be a
-- much worse thing to leak. Stored as a SHA-256 digest for the same reason the sync service
-- stores its own that way -- a copy of this file is not a set of working credentials.
CREATE TABLE IF NOT EXISTS desktop_token (
    token_sha256    TEXT PRIMARY KEY,
    discord_user_id TEXT NOT NULL REFERENCES account(discord_user_id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_used_at    TEXT
);

CREATE INDEX IF NOT EXISTS desktop_token_by_account ON desktop_token(discord_user_id);

-- An item someone wants to keep an eye on without it having to clear GE Flipper's own ranking
-- bar. Just the id: the name and every price on the watchlist page comes from the live market
-- snapshot at render time, the same as everywhere else prices are shown.
CREATE TABLE IF NOT EXISTS watchlist_item (
    discord_user_id TEXT NOT NULL REFERENCES account(discord_user_id) ON DELETE CASCADE,
    item_id         INTEGER NOT NULL,
    added_at        TEXT NOT NULL,
    -- An armed alert has both a target and a direction; either both are set or neither is.
    target_price    INTEGER,
    direction       TEXT CHECK (direction IN ('above', 'below')),
    last_alerted_at TEXT,
    PRIMARY KEY (discord_user_id, item_id)
);
"""


def _stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Account:
    discord_user_id: str
    username: str
    avatar: str | None
    pairing_token: str | None
    created_at: str
    last_login_at: str
    goal_label: str | None
    goal_target: int | None
    goal_created_at: str | None
    discord_webhook_url: str | None

    @property
    def paired(self) -> bool:
        return bool(self.pairing_token)

    @property
    def has_goal(self) -> bool:
        return self.goal_target is not None


@dataclass(frozen=True, slots=True)
class Character:
    character_id: int
    name: str
    account_hash: str | None


@dataclass(frozen=True, slots=True)
class DesktopToken:
    token_sha256: str
    label: str
    created_at: str
    last_used_at: str | None


@dataclass(frozen=True, slots=True)
class WatchlistAlert:
    item_id: int
    target_price: int | None
    direction: str | None
    last_alerted_at: str | None


@dataclass(frozen=True, slots=True)
class ArmedAlert:
    discord_user_id: str
    discord_webhook_url: str
    item_id: int
    target_price: int
    direction: str
    last_alerted_at: str | None


_MIGRATION_COLUMNS = {
    "goal_label": "TEXT",
    "goal_target": "INTEGER",
    "goal_created_at": "TEXT",
    "discord_webhook_url": "TEXT",
}

_WATCHLIST_MIGRATION_COLUMNS = {
    "target_price": "INTEGER",
    "direction": "TEXT",
    "last_alerted_at": "TEXT",
}


class AccountsDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(account)")}
            for column, sql_type in _MIGRATION_COLUMNS.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE account ADD COLUMN {column} {sql_type}")
            existing_watchlist_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(watchlist_item)")
            }
            for column, sql_type in _WATCHLIST_MIGRATION_COLUMNS.items():
                if column not in existing_watchlist_columns:
                    connection.execute(f"ALTER TABLE watchlist_item ADD COLUMN {column} {sql_type}")
            self._rekey_trade_characters(connection)

    def _rekey_trade_characters(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(trade_character)")
        }
        if not columns or "trade_sync_uid" in columns:
            return
        stranded = connection.execute("SELECT COUNT(*) FROM trade_character").fetchone()[0]
        if stranded:
            connection.execute("ALTER TABLE trade_character RENAME TO trade_character_by_local_id")
        else:
            connection.execute("DROP TABLE trade_character")
        connection.execute(
            """
            CREATE TABLE trade_character (
                discord_user_id TEXT NOT NULL REFERENCES account(discord_user_id) ON DELETE CASCADE,
                trade_sync_uid  TEXT NOT NULL,
                character_id    INTEGER NOT NULL REFERENCES character(character_id)
                    ON DELETE CASCADE,
                PRIMARY KEY (discord_user_id, trade_sync_uid)
            )
            """
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def upsert_login(self, discord_user_id: str, username: str, avatar: str | None) -> Account:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO account (discord_user_id, username, avatar, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(discord_user_id) DO UPDATE SET"
                "  username = excluded.username,"
                "  avatar = excluded.avatar,"
                "  last_login_at = excluded.last_login_at",
                (discord_user_id, username, avatar, _stamp(), _stamp()),
            )
        return self.get(discord_user_id)

    def get(self, discord_user_id: str) -> Account | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM account WHERE discord_user_id = ?", (discord_user_id,)
            ).fetchone()
        return _row_to_account(row) if row else None

    def set_display_name(self, discord_user_id: str, username: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET username = ? WHERE discord_user_id = ?",
                (username, discord_user_id),
            )

    def set_pairing_token(self, discord_user_id: str, token: str | None) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET pairing_token = ? WHERE discord_user_id = ?",
                (token, discord_user_id),
            )

    def set_discord_webhook(self, discord_user_id: str, url: str | None) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET discord_webhook_url = ? WHERE discord_user_id = ?",
                (url, discord_user_id),
            )

    def set_savings_goal(self, discord_user_id: str, label: str, target: int) -> None:
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET goal_label = ?, goal_target = ?, goal_created_at = ?"
                " WHERE discord_user_id = ?",
                (label, target, created_at, discord_user_id),
            )

    def clear_savings_goal(self, discord_user_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET goal_label = NULL, goal_target = NULL,"
                " goal_created_at = NULL WHERE discord_user_id = ?",
                (discord_user_id,),
            )


    def add_character(self, discord_user_id: str, name: str) -> Character | None:
        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    "INSERT INTO character (discord_user_id, name, created_at) VALUES (?, ?, ?)",
                    (discord_user_id, name, _stamp()),
                )
                character_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return None
        return Character(character_id=character_id, name=name, account_hash=None)

    def list_characters(self, discord_user_id: str) -> list[Character]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT character_id, name, account_hash FROM character"
                " WHERE discord_user_id = ? ORDER BY name",
                (discord_user_id,),
            ).fetchall()
        return [
            Character(
                character_id=row["character_id"], name=row["name"], account_hash=row["account_hash"]
            )
            for row in rows
        ]

    def delete_character(self, discord_user_id: str, character_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM character WHERE discord_user_id = ? AND character_id = ?",
                (discord_user_id, character_id),
            )

    def set_trade_character(
        self, discord_user_id: str, trade_sync_uid: str, character_id: int | None
    ) -> None:
        with self._transaction() as connection:
            if character_id is None:
                connection.execute(
                    "DELETE FROM trade_character WHERE discord_user_id = ? AND trade_sync_uid = ?",
                    (discord_user_id, trade_sync_uid),
                )
                return
            connection.execute(
                "INSERT INTO trade_character (discord_user_id, trade_sync_uid, character_id)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(discord_user_id, trade_sync_uid)"
                " DO UPDATE SET character_id = excluded.character_id",
                (discord_user_id, trade_sync_uid, character_id),
            )

    def get_trade_characters(self, discord_user_id: str) -> dict[str, int]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT trade_sync_uid, character_id FROM trade_character"
                " WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchall()
        return {row["trade_sync_uid"]: row["character_id"] for row in rows}


    def add_watchlist_item(self, discord_user_id: str, item_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO watchlist_item (discord_user_id, item_id, added_at)"
                " VALUES (?, ?, ?)",
                (discord_user_id, item_id, _stamp()),
            )
            return cursor.rowcount > 0

    def list_watchlist_item_ids(self, discord_user_id: str) -> list[int]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT item_id FROM watchlist_item WHERE discord_user_id = ? ORDER BY added_at",
                (discord_user_id,),
            ).fetchall()
        return [row["item_id"] for row in rows]

    def remove_watchlist_item(self, discord_user_id: str, item_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM watchlist_item WHERE discord_user_id = ? AND item_id = ?",
                (discord_user_id, item_id),
            )

    def list_watchlist_alerts(self, discord_user_id: str) -> list[WatchlistAlert]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT item_id, target_price, direction, last_alerted_at FROM watchlist_item"
                " WHERE discord_user_id = ? ORDER BY added_at",
                (discord_user_id,),
            ).fetchall()
        return [
            WatchlistAlert(
                item_id=row["item_id"],
                target_price=row["target_price"],
                direction=row["direction"],
                last_alerted_at=row["last_alerted_at"],
            )
            for row in rows
        ]

    def set_watchlist_alert(
        self, discord_user_id: str, item_id: int, target_price: int, direction: str
    ) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE watchlist_item SET target_price = ?, direction = ?, last_alerted_at = NULL"
                " WHERE discord_user_id = ? AND item_id = ?",
                (target_price, direction, discord_user_id, item_id),
            )
            return cursor.rowcount > 0

    def clear_watchlist_alert(self, discord_user_id: str, item_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE watchlist_item SET target_price = NULL, direction = NULL,"
                " last_alerted_at = NULL WHERE discord_user_id = ? AND item_id = ?",
                (discord_user_id, item_id),
            )

    def list_armed_alerts(self) -> list[ArmedAlert]:
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT w.discord_user_id AS discord_user_id, a.discord_webhook_url AS webhook,
                       w.item_id AS item_id, w.target_price AS target_price,
                       w.direction AS direction, w.last_alerted_at AS last_alerted_at
                FROM watchlist_item w
                JOIN account a ON a.discord_user_id = w.discord_user_id
                WHERE w.target_price IS NOT NULL
                  AND w.direction IS NOT NULL
                  AND a.discord_webhook_url IS NOT NULL
                """
            ).fetchall()
        return [
            ArmedAlert(
                discord_user_id=row["discord_user_id"],
                discord_webhook_url=row["webhook"],
                item_id=row["item_id"],
                target_price=row["target_price"],
                direction=row["direction"],
                last_alerted_at=row["last_alerted_at"],
            )
            for row in rows
        ]

    def stamp_alert_sent(self, discord_user_id: str, item_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE watchlist_item SET last_alerted_at = ?"
                " WHERE discord_user_id = ? AND item_id = ?",
                (_stamp(), discord_user_id, item_id),
            )


    def create_desktop_token(self, discord_user_id: str, label: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO desktop_token (token_sha256, discord_user_id, label, created_at)"
                " VALUES (?, ?, ?, ?)",
                (_digest(token), discord_user_id, label, _stamp()),
            )
        return token

    def account_id_for_desktop_token(self, token: str) -> str | None:
        digest = _digest(token)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT discord_user_id, last_used_at FROM desktop_token WHERE token_sha256 = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            if _older_than_a_minute(row["last_used_at"]):
                connection.execute(
                    "UPDATE desktop_token SET last_used_at = ? WHERE token_sha256 = ?",
                    (_stamp(), digest),
                )
        return row["discord_user_id"]

    def list_desktop_tokens(self, discord_user_id: str) -> list[DesktopToken]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT token_sha256, label, created_at, last_used_at FROM desktop_token"
                " WHERE discord_user_id = ? ORDER BY created_at DESC",
                (discord_user_id,),
            ).fetchall()
        return [
            DesktopToken(
                token_sha256=row["token_sha256"],
                label=row["label"],
                created_at=row["created_at"],
                last_used_at=row["last_used_at"],
            )
            for row in rows
        ]

    def revoke_desktop_token(self, discord_user_id: str, token_sha256: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM desktop_token WHERE discord_user_id = ? AND token_sha256 = ?",
                (discord_user_id, token_sha256),
            )
            return cursor.rowcount > 0

    def delete_account(self, discord_user_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM account WHERE discord_user_id = ?", (discord_user_id,)
            )
            return cursor.rowcount > 0


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _older_than_a_minute(stamp: str | None) -> bool:
    if not stamp:
        return True
    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    return (datetime.now(UTC) - seen).total_seconds() > 60


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        discord_user_id=row["discord_user_id"],
        username=row["username"],
        avatar=row["avatar"],
        pairing_token=row["pairing_token"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
        goal_label=row["goal_label"],
        goal_target=row["goal_target"],
        goal_created_at=row["goal_created_at"],
        discord_webhook_url=row["discord_webhook_url"],
    )
