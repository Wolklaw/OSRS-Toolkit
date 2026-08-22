"""Who has an account here, and what pairing token their journal reads through.

Deliberately separate from any one user's journal database. A journal is per-Discord-account
data that lives under its own file; this is the small shared table that says which journal
belongs to which login and which sync pairing feeds it. Losing this file loses nobody's trades
— it costs a re-login and re-pasting a token, not a rebuild of anyone's history.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    discord_user_id TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    avatar          TEXT,
    pairing_token   TEXT,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT NOT NULL
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

    @property
    def paired(self) -> bool:
        return bool(self.pairing_token)


class AccountsDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
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
        """Record a successful sign-in, creating the account on its first one.

        The username and avatar are refreshed on every login — Discord display names change,
        and there is no reason to keep showing someone a stale one when the truth arrives free
        with every sign-in.
        """
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

    def set_pairing_token(self, discord_user_id: str, token: str | None) -> None:
        """Attach a sync pairing token to this login, or remove one with ``None``.

        Not validated here — the caller is expected to have already asked the sync service
        whether the token means anything, since that is the only place that can answer. This
        just remembers what came back yes.
        """
        with self._transaction() as connection:
            connection.execute(
                "UPDATE account SET pairing_token = ? WHERE discord_user_id = ?",
                (token, discord_user_id),
            )


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        discord_user_id=row["discord_user_id"],
        username=row["username"],
        avatar=row["avatar"],
        pairing_token=row["pairing_token"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )
