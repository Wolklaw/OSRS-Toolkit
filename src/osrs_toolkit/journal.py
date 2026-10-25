from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from osrs_toolkit.ranking import ge_tax, is_tax_exempt

MAX_NET_WORTH_HISTORY_PER_ACCOUNT = 2_000

MAX_SKILLS_HISTORY_PER_ACCOUNT = 2_000

SNAPSHOT_HISTORY_FULL_DAYS = 30
SNAPSHOT_HISTORY_DAILY_DAYS = 365

MAX_SQL_PARAMETERS = 500

BUSY_TIMEOUT_SECONDS = 15.0

NOT_BEFORE_SAFETY_MARGIN = timedelta(days=1)

DEFAULT_FLIP_TRACKING_MODE = "session"


def _instant(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _chunks(values: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _fills_for(
    connection: sqlite3.Connection, table: str, position_ids: Sequence[int]
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for chunk in _chunks(position_ids, MAX_SQL_PARAMETERS):
        placeholders = ",".join("?" * len(chunk))
        rows.extend(
            connection.execute(
                f"SELECT * FROM {table} WHERE position_id IN ({placeholders})"
                " ORDER BY fill_id ASC",
                tuple(chunk),
            ).fetchall()
        )
    return rows


def _trading_session(row: sqlite3.Row) -> TradingSession:
    return TradingSession(
        session_id=int(row["session_id"]),
        account_hash=str(row["account_hash"]) if row["account_hash"] is not None else None,
        label=str(row["label"]),
        started_at=str(row["started_at"]),
        ended_at=str(row["ended_at"]) if row["ended_at"] is not None else None,
    )


@dataclass(frozen=True, slots=True)
class TradingSession:
    session_id: int
    account_hash: str | None
    label: str
    started_at: str
    ended_at: str | None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: int
    recorded_at: str
    item_name: str
    quantity: int
    buy_price: int
    sell_price: int
    sync_uid: str | None = None

    @property
    def tax_each(self) -> int:
        return ge_tax(self.sell_price, exempt=is_tax_exempt(item_name=self.item_name))

    @property
    def invested(self) -> int:
        return self.buy_price * self.quantity

    @property
    def profit(self) -> int:
        return (self.sell_price - self.buy_price - self.tax_each) * self.quantity

    @property
    def roi(self) -> float:
        return self.profit / self.invested * 100 if self.invested else 0.0


@dataclass(frozen=True, slots=True)
class SaleFill:
    fill_id: int
    position_id: int
    quantity: int
    sell_price: int


@dataclass(frozen=True, slots=True)
class BuyFill:
    fill_id: int
    position_id: int
    quantity: int
    buy_price: int


@dataclass(frozen=True, slots=True)
class TrackedTrade:
    position_id: int
    created_at: str
    item_id: int | None
    item_name: str
    quantity: int
    target_buy: int
    target_sell: int
    actual_buy: int | None
    actual_sell: int | None
    status: str
    sale_fills: tuple[SaleFill, ...] = ()
    buy_fills: tuple[BuyFill, ...] = ()
    strategy: str = "Balanced (1–4h)"
    current_buy_suggestion: int | None = None
    current_sell_suggestion: int | None = None
    suggestion_reviewed_at: str | None = None
    completed_at: str | None = None
    listed_sell_price: int | None = None
    account_hash: str | None = None

    @property
    def tax_exempt(self) -> bool:
        return is_tax_exempt(self.item_id, self.item_name)

    @property
    def estimated_profit(self) -> int:
        buy_price = self.actual_buy or self.buy_suggestion
        sell_price = self.sell_suggestion
        tax = ge_tax(sell_price, exempt=self.tax_exempt)
        return (sell_price - buy_price - tax) * self.unsold_stock

    @property
    def buy_suggestion(self) -> int:
        return self.current_buy_suggestion or self.target_buy

    @property
    def sell_suggestion(self) -> int:
        return self.current_sell_suggestion or self.target_sell

    @property
    def asking_price(self) -> int:
        return self.listed_sell_price or self.sell_suggestion

    @property
    def suggestion_was_refreshed(self) -> bool:
        return bool(
            self.suggestion_reviewed_at and self.suggestion_reviewed_at[:10] > self.created_at[:10]
        )

    @property
    def realized_profit(self) -> int | None:
        if self.actual_buy is None or not self.sale_fills:
            return None
        exempt = self.tax_exempt
        return sum(
            (fill.sell_price - self.actual_buy - ge_tax(fill.sell_price, exempt=exempt))
            * fill.quantity
            for fill in self.sale_fills
        )

    @property
    def sold_quantity(self) -> int:
        return sum(fill.quantity for fill in self.sale_fills)

    @property
    def unsold_stock(self) -> int:
        if self.buy_fills:
            return max(0, self.bought_quantity - self.sold_quantity)
        return self.remaining_quantity

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.quantity - self.sold_quantity)

    @property
    def average_sell_price(self) -> int | None:
        if not self.sale_fills or self.sold_quantity == 0:
            return None
        proceeds = sum(fill.sell_price * fill.quantity for fill in self.sale_fills)
        return round(proceeds / self.sold_quantity)

    @property
    def bought_quantity(self) -> int:
        return sum(fill.quantity for fill in self.buy_fills)

    @property
    def average_buy_price(self) -> int | None:
        if not self.buy_fills or self.bought_quantity == 0:
            return None
        cost = sum(fill.buy_price * fill.quantity for fill in self.buy_fills)
        return round(cost / self.bought_quantity)

    @property
    def invested(self) -> int:
        if self.buy_fills:
            return sum(fill.buy_price * fill.quantity for fill in self.buy_fills)
        buy_price = self.actual_buy if self.actual_buy is not None else self.target_buy
        return buy_price * self.quantity


@dataclass(frozen=True, slots=True)
class SyncedItem:
    flow: str
    item_id: int
    item_name: str
    quantity: int
    unit_value: int

    @property
    def total_value(self) -> int:
        return self.quantity * self.unit_value


@dataclass(frozen=True, slots=True)
class SyncedTrade:
    event_id: str
    occurred_at: str
    event_type: str
    account_hash: str
    account_name: str
    counterparty: str | None
    direction: str
    metadata: dict[str, object]
    items: tuple[SyncedItem, ...]

    @property
    def source(self) -> str:
        return "Grand Exchange" if self.event_type == "ge_fill" else "Player trade"

    @property
    def given(self) -> tuple[SyncedItem, ...]:
        return tuple(item for item in self.items if item.flow == "given")

    @property
    def received(self) -> tuple[SyncedItem, ...]:
        return tuple(item for item in self.items if item.flow == "received")

    @property
    def given_value(self) -> int:
        return sum(item.total_value for item in self.given)

    @property
    def received_value(self) -> int:
        return sum(item.total_value for item in self.received)

    @property
    def estimated_difference(self) -> int:
        return self.received_value - self.given_value


@dataclass(frozen=True, slots=True)
class LoadoutItem:
    item_id: int
    item_name: str
    quantity: int
    unit_value: int

    @property
    def total_value(self) -> int:
        return self.quantity * self.unit_value


@dataclass(frozen=True, slots=True)
class LoadoutSnapshot:
    account_hash: str
    account_name: str
    captured_at: str
    equipment: tuple[LoadoutItem, ...]
    inventory: tuple[LoadoutItem, ...]
    bank: tuple[LoadoutItem, ...]
    skills: dict[str, int]
    xp: dict[str, int] = field(default_factory=dict)

    @property
    def owned_item_ids(self) -> frozenset[int]:
        return frozenset(item.item_id for item in (*self.equipment, *self.inventory, *self.bank))

    def quantity_owned(self, item_id: int) -> int:
        return sum(
            item.quantity
            for item in (*self.equipment, *self.inventory, *self.bank)
            if item.item_id == item_id
        )

    @property
    def total_value(self) -> int:
        return sum(item.total_value for item in (*self.equipment, *self.inventory, *self.bank))


@dataclass(frozen=True, slots=True)
class NpcLootRecord:
    event_id: str
    occurred_at: str
    account_hash: str
    account_name: str
    npc_name: str
    items: tuple[LoadoutItem, ...]

    @property
    def total_value(self) -> int:
        return sum(item.total_value for item in self.items)


@dataclass(frozen=True, slots=True)
class PlayerDeathRecord:
    event_id: str
    occurred_at: str
    account_hash: str
    account_name: str
    skulled: bool
    equipment: tuple[LoadoutItem, ...]
    inventory: tuple[LoadoutItem, ...]

    @property
    def total_value(self) -> int:
        return sum(item.total_value for item in (*self.equipment, *self.inventory))


@dataclass(frozen=True, slots=True)
class NetWorthPoint:
    account_hash: str
    account_name: str
    captured_at: str
    total_value: int


@dataclass(frozen=True, slots=True)
class SkillsPoint:
    account_hash: str
    account_name: str
    captured_at: str
    skills: dict[str, int]
    xp: dict[str, int] = field(default_factory=dict)

    @property
    def total_level(self) -> int:
        return sum(self.skills.values())


SESSION_GAP_MINUTES = 30


@dataclass(frozen=True, slots=True)
class LootByNpc:
    npc_name: str
    kills: int
    value: int


@dataclass(frozen=True, slots=True)
class SessionSummary:
    account_hash: str
    account_name: str
    started_at: str
    ended_at: str
    duration_minutes: int
    loot_value: int
    loot_by_npc: tuple[LootByNpc, ...]
    death_count: int
    value_lost: int
    trade_count: int
    trade_net_value: int
    xp_gained: dict[str, int]
    levels_gained: dict[str, int]
    net_worth_change: int | None


@dataclass(frozen=True, slots=True)
class OfferOpened:
    event_id: str
    account_hash: str
    account_name: str
    occurred_at: str
    side: str
    item_id: int
    item_name: str
    offer_price: int
    total_quantity: int
    restored: bool = False


@dataclass(frozen=True, slots=True)
class OfferCancelled:
    event_id: str
    account_hash: str
    account_name: str
    occurred_at: str
    side: str
    item_id: int
    item_name: str
    offer_price: int
    total_quantity: int


def _is_newer(incoming: object, existing: object) -> bool:
    return _as_moment(incoming) >= _as_moment(existing)


def _as_moment(stamp: object) -> datetime:
    if not isinstance(stamp, str) or not stamp:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _changed_snapshot_moments[T](
    history: list[T], differs: Callable[[T, T], bool]
) -> list[datetime]:
    moments: list[datetime] = []
    previous: T | None = None
    for point in history:
        if previous is not None and differs(previous, point):
            moments.append(_as_moment(previous.captured_at))  # type: ignore[attr-defined]
            moments.append(_as_moment(point.captured_at))  # type: ignore[attr-defined]
        previous = point
    return moments


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Unchanged:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "UNCHANGED"


UNCHANGED = _Unchanged()


@dataclass(frozen=True, slots=True)
class RetagResult:
    updated: int
    skipped: tuple[int, ...] = ()


class JournalRepository:
    _TERMINAL_STATUSES = frozenset({"Completed", "Cancelled"})
    FLIP_TRACKING_MODES: ClassVar[frozenset[str]] = frozenset({"session", "always"})
    _ALL_STATUSES = frozenset(
        {
            "Pending buy",
            "Bought",
            "Listed for sale",
            "Partially sold",
            "Completed",
            "Cancelled",
            "Supplies",
        }
    )

    _SYNC_COLUMNS: ClassVar[dict[str, str]] = {
        "sync_uid": "TEXT",
        "updated_at": "TEXT",
        "deleted_at": "TEXT",
    }

    def __init__(self, database_path: Path | None = None) -> None:
        using_default_path = database_path is None
        if database_path is None:
            local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
            database_path = local_data / "OSRSToolkit" / "data" / "toolkit.db"
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if using_default_path:
            self._recover_or_migrate_default_database()
            self._backup_before_migration()
        self._initialize()
        if using_default_path:
            self._create_startup_backup()

    def _backup_before_migration(self) -> None:
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return
        try:
            with closing(sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                pending = False
                for table in ("trades", "tracked_trades"):
                    columns = {
                        str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")
                    }
                    if not columns:
                        continue
                    if not self._SYNC_COLUMNS.keys() <= columns:
                        pending = True
                if not pending:
                    return
                roaming_data = Path(os.getenv("APPDATA", Path.home()))
                backup_dir = roaming_data / "OSRSToolkit" / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                destination_path = backup_dir / f"toolkit-premigration-{stamp}.db"
                with closing(sqlite3.connect(destination_path)) as destination:
                    db.backup(destination)
        except sqlite3.Error:
            return

    def _recover_or_migrate_default_database(self) -> None:
        if self.database_path.exists():
            return
        roaming_data = Path(os.getenv("APPDATA", Path.home()))
        local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
        backup_dir = roaming_data / "OSRSToolkit" / "backups"
        candidates = [
            *backup_dir.glob("toolkit-*.db"),
            local_data / "OSRS Toolkit" / "data" / "toolkit.db",
            roaming_data / "OSRSToolkit" / "data" / "toolkit.db",
            roaming_data / "OSRS Toolkit" / "data" / "toolkit.db",
            Path(sys.executable).resolve().parent / "data" / "toolkit.db",
            Path.cwd() / "data" / "toolkit.db",
        ]
        existing = [path for path in candidates if path.is_file() and path.stat().st_size > 0]
        if existing:
            newest = max(existing, key=lambda path: path.stat().st_mtime)
            shutil.copy2(newest, self.database_path)

    def _create_startup_backup(self) -> None:
        with self._connect() as source:
            record_count = sum(
                int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("trades", "tracked_trades", "synced_trades")
            )
            if record_count == 0:
                return
            roaming_data = Path(os.getenv("APPDATA", Path.home()))
            backup_dir = roaming_data / "OSRSToolkit" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"toolkit-{timestamp}.db"
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)
        backups = sorted(backup_dir.glob("toolkit-*.db"), key=lambda path: path.stat().st_mtime)
        for old_backup in backups[:-10]:
            old_backup.unlink(missing_ok=True)

    _SYNC_TABLES: ClassVar[dict[str, str]] = {
        "trades": "trade_id",
        "tracked_trades": "position_id",
    }

    _FILL_TABLES: ClassVar[dict[str, tuple[str, ...]]] = {
        "tracked_sale_fills": ("quantity", "sell_price"),
        "tracked_buy_fills": ("quantity", "buy_price"),
    }

    def sync_version(self) -> dict:
        with self._connect() as connection:
            latest = ""
            counts = {}
            for table in self._SYNC_TABLES:
                row = connection.execute(
                    f"SELECT MAX(updated_at) AS latest, COUNT(*) AS total FROM {table}"
                ).fetchone()
                counts[table] = int(row["total"])
                latest = max(latest, str(row["latest"] or ""))
        return {"version": latest, "counts": counts}

    def sync_export(self, since: str | None = None) -> dict:
        payload: dict[str, list[dict]] = {}
        with self._connect() as connection:
            for table, primary_key in self._SYNC_TABLES.items():
                if since:
                    rows = connection.execute(
                        f"SELECT * FROM {table} WHERE updated_at > ? ORDER BY updated_at",
                        (since,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        f"SELECT * FROM {table} ORDER BY updated_at"
                    ).fetchall()
                exported = []
                for row in rows:
                    record = {
                        key: row[key]
                        for key in row.keys()  # noqa: SIM118
                        if key != primary_key
                    }
                    if not record.get("sync_uid"):
                        continue
                    if table == "tracked_trades":
                        for fill_table, columns in self._FILL_TABLES.items():
                            fills = connection.execute(
                                f"SELECT {', '.join(columns)} FROM {fill_table}"
                                " WHERE position_id = ? ORDER BY fill_id",
                                (row[primary_key],),
                            ).fetchall()
                            record[fill_table] = [dict(fill) for fill in fills]
                    exported.append(record)
                payload[table] = exported
        return payload

    def sync_apply(self, payload: dict) -> dict[str, int]:
        applied = {"inserted": 0, "updated": 0, "skipped": 0}
        with self._connect() as connection:
            for table, primary_key in self._SYNC_TABLES.items():
                known = {
                    str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for record in payload.get(table) or []:
                    if not isinstance(record, dict):
                        applied["skipped"] += 1
                        continue
                    uid = record.get("sync_uid")
                    if not uid:
                        applied["skipped"] += 1
                        continue
                    columns = {
                        key: value
                        for key, value in record.items()
                        if key in known and key != primary_key
                    }
                    existing = connection.execute(
                        f"SELECT {primary_key}, updated_at FROM {table} WHERE sync_uid = ?",
                        (uid,),
                    ).fetchone()
                    if existing is None:
                        names = ", ".join(columns)
                        placeholders = ", ".join("?" for _ in columns)
                        cursor = connection.execute(
                            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                            tuple(columns.values()),
                        )
                        row_id = int(cursor.lastrowid)
                        applied["inserted"] += 1
                    elif _is_newer(record.get("updated_at"), existing["updated_at"]):
                        assignments = ", ".join(f"{name} = ?" for name in columns)
                        connection.execute(
                            f"UPDATE {table} SET {assignments} WHERE sync_uid = ?",
                            (*columns.values(), uid),
                        )
                        row_id = int(existing[primary_key])
                        applied["updated"] += 1
                    else:
                        applied["skipped"] += 1
                        continue
                    if table == "tracked_trades":
                        self._replace_fills(connection, row_id, record)
        return applied

    def _replace_fills(
        self, connection: sqlite3.Connection, position_id: int, record: dict
    ) -> None:
        for fill_table, fill_columns in self._FILL_TABLES.items():
            incoming = record.get(fill_table)
            if incoming is None:
                continue
            connection.execute(f"DELETE FROM {fill_table} WHERE position_id = ?", (position_id,))
            names = ", ".join(("position_id", *fill_columns))
            placeholders = ", ".join("?" for _ in range(len(fill_columns) + 1))
            connection.executemany(
                f"INSERT INTO {fill_table} ({names}) VALUES ({placeholders})",
                [
                    (position_id, *(fill.get(column) for column in fill_columns))
                    for fill in incoming
                    if isinstance(fill, dict)
                ],
            )

    def _install_touch_triggers(self, connection: sqlite3.Connection) -> None:
        for table, primary_key in self._SYNC_TABLES.items():
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_touch_updated_at
                AFTER UPDATE ON {table}
                FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
                BEGIN
                    UPDATE {table}
                    SET updated_at = strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now')
                    WHERE {primary_key} = NEW.{primary_key};
                END
                """
            )

    @staticmethod
    def _add_missing_columns(
        connection: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_sync_columns(
        self, connection: sqlite3.Connection, table: str, primary_key: str, stamp_from: str
    ) -> None:
        existing = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in self._SYNC_COLUMNS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        connection.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_sync_uid ON {table}(sync_uid)"
            " WHERE sync_uid IS NOT NULL"
        )
        connection.execute(f"CREATE INDEX IF NOT EXISTS {table}_updated_at ON {table}(updated_at)")
        connection.execute(f"UPDATE {table} SET updated_at = {stamp_from} WHERE updated_at IS NULL")
        unnamed = connection.execute(
            f"SELECT {primary_key} FROM {table} WHERE sync_uid IS NULL"
        ).fetchall()
        for row in unnamed:
            connection.execute(
                f"UPDATE {table} SET sync_uid = ? WHERE {primary_key} = ?",
                (uuid.uuid4().hex, row[primary_key]),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            for table in self._SYNC_TABLES:
                connection.execute(f"DROP TRIGGER IF EXISTS {table}_touch_updated_at")
            self._create_trade_tables(connection)
            self._create_synced_trade_tables(connection)
            self._create_activity_tables(connection)
            self._create_loadout_tables(connection)
            self._create_timeseries_tables(connection)
            self._create_position_tables(connection)
            self._create_session_tables(connection)
            self._install_touch_triggers(connection)

    def _create_trade_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                buy_price INTEGER NOT NULL CHECK (buy_price >= 0),
                sell_price INTEGER NOT NULL CHECK (sell_price >= 0)
            )
            """
        )
        self._migrate_sync_columns(connection, "trades", "trade_id", "recorded_at")

    def _create_synced_trade_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_trades (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                counterparty TEXT,
                direction TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_trade_items (
                item_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES synced_trades(event_id) ON DELETE CASCADE,
                flow TEXT NOT NULL CHECK (flow IN ('given', 'received')),
                item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_value INTEGER NOT NULL CHECK (unit_value >= 0)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_offer_events (
                event_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS synced_trades_occurred ON synced_trades(occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS synced_trade_items_event "
            "ON synced_trade_items(event_id)"
        )

    def _create_activity_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS npc_loot_events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                npc_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS npc_loot_items (
                item_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES npc_loot_events(event_id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_value INTEGER NOT NULL CHECK (unit_value >= 0)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS npc_loot_events_occurred "
            "ON npc_loot_events(occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS npc_loot_items_event ON npc_loot_items(event_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS player_death_events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                skulled INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS player_death_items (
                item_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL
                    REFERENCES player_death_events(event_id) ON DELETE CASCADE,
                flow TEXT NOT NULL CHECK (flow IN ('equipment', 'inventory')),
                item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_value INTEGER NOT NULL CHECK (unit_value >= 0)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS player_death_events_occurred "
            "ON player_death_events(occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS player_death_items_event "
            "ON player_death_items(event_id)"
        )

    def _create_loadout_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS loadout_snapshots (
                account_hash TEXT PRIMARY KEY,
                account_name TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                equipment_json TEXT NOT NULL,
                inventory_json TEXT NOT NULL,
                bank_json TEXT NOT NULL,
                skills_json TEXT NOT NULL
            )
            """
        )
        self._add_missing_columns(
            connection, "loadout_snapshots", {"xp_json": "TEXT NOT NULL DEFAULT '{}'"}
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS loadout_snapshot_history (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                equipment_json TEXT NOT NULL,
                inventory_json TEXT NOT NULL,
                bank_json TEXT NOT NULL,
                skills_json TEXT NOT NULL,
                xp_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS loadout_snapshot_history_captured "
            "ON loadout_snapshot_history(account_hash, captured_at)"
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO loadout_snapshot_history (
                account_hash, account_name, captured_at, equipment_json, inventory_json,
                bank_json, skills_json, xp_json
            )
            SELECT account_hash, account_name, captured_at, equipment_json,
                   inventory_json, bank_json, skills_json, xp_json
            FROM loadout_snapshots
            """
        )

    def _create_timeseries_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS net_worth_history (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                total_value INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS net_worth_history_captured "
            "ON net_worth_history(captured_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS skills_history (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_hash TEXT NOT NULL,
                account_name TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                skills_json TEXT NOT NULL
            )
            """
        )
        self._add_missing_columns(
            connection, "skills_history", {"xp_json": "TEXT NOT NULL DEFAULT '{}'"}
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS skills_history_captured ON skills_history(captured_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quest_progress (
                account_hash TEXT NOT NULL,
                quest_name TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (account_hash, quest_name)
            )
            """
        )

    def _create_position_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_trades (
                position_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                item_id INTEGER,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                target_buy INTEGER NOT NULL CHECK (target_buy >= 0),
                target_sell INTEGER NOT NULL CHECK (target_sell >= 0),
                actual_buy INTEGER,
                actual_sell INTEGER,
                status TEXT NOT NULL
            )
            """
        )
        tracked_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(tracked_trades)").fetchall()
        }
        column_migrations = {
            "strategy": "TEXT NOT NULL DEFAULT 'Balanced (1–4h)'",
            "current_buy_suggestion": "INTEGER",
            "current_sell_suggestion": "INTEGER",
            "suggestion_reviewed_at": "TEXT",
            "completed_at": "TEXT",
            "listed_sell_price": "INTEGER",
            "account_hash": "TEXT",
        }
        for column, definition in column_migrations.items():
            if column not in tracked_columns:
                connection.execute(
                    f"ALTER TABLE tracked_trades ADD COLUMN {column} {definition}"
                )
        self._migrate_sync_columns(connection, "tracked_trades", "position_id", "created_at")
        connection.execute(
            """
            UPDATE tracked_trades
            SET strategy = ?
            WHERE strategy LIKE 'Balanced (%4h)' AND strategy <> ?
            """,
            ("Balanced (1–4h)", "Balanced (1–4h)"),
        )
        connection.execute(
            """
            UPDATE tracked_trades
            SET current_buy_suggestion = COALESCE(current_buy_suggestion, target_buy),
                current_sell_suggestion = COALESCE(current_sell_suggestion, target_sell),
                suggestion_reviewed_at = COALESCE(suggestion_reviewed_at, created_at)
            """
        )
        connection.execute(
            """
            UPDATE tracked_trades
            SET completed_at = created_at
            WHERE completed_at IS NULL AND status IN ('Completed', 'Cancelled')
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_sale_fills (
                fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL
                    REFERENCES tracked_trades(position_id) ON DELETE CASCADE,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                sell_price INTEGER NOT NULL CHECK (sell_price >= 0)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tracked_sale_fills (position_id, quantity, sell_price)
            SELECT position_id, quantity, actual_sell
            FROM tracked_trades
            WHERE status = 'Completed'
              AND actual_sell IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tracked_sale_fills
                  WHERE tracked_sale_fills.position_id = tracked_trades.position_id
              )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_buy_fills (
                fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL
                    REFERENCES tracked_trades(position_id) ON DELETE CASCADE,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                buy_price INTEGER NOT NULL CHECK (buy_price >= 0)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tracked_buy_fills (position_id, quantity, buy_price)
            SELECT position_id, quantity, actual_buy
            FROM tracked_trades
            WHERE actual_buy IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tracked_buy_fills
                  WHERE tracked_buy_fills.position_id = tracked_trades.position_id
              )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS tracked_sale_fills_position "
            "ON tracked_sale_fills(position_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS tracked_buy_fills_position "
            "ON tracked_buy_fills(position_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS tracked_trades_live "
            "ON tracked_trades(deleted_at, account_hash)"
        )

    def _create_session_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_hash TEXT,
                label TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS trading_sessions_open "
            "ON trading_sessions(ended_at, account_hash)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    def _setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM journal_settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def _write_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO journal_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def flip_tracking_mode(self) -> str:
        value = self._setting("flip_tracking_mode")
        return value if value in self.FLIP_TRACKING_MODES else DEFAULT_FLIP_TRACKING_MODE

    def set_flip_tracking_mode(self, mode: str) -> None:
        if mode not in self.FLIP_TRACKING_MODES:
            raise ValueError("mode must be 'session' or 'always'")
        self._write_setting("flip_tracking_mode", mode)

    def start_trading_session(
        self,
        account_hash: str | None = None,
        label: str = "",
        started_at: str | None = None,
    ) -> TradingSession:
        now = _now()
        opened_at = started_at or now
        with self._connect() as connection:
            connection.execute(
                "UPDATE trading_sessions SET ended_at = ?"
                " WHERE ended_at IS NULL AND (account_hash IS ? OR account_hash IS NULL)",
                (now, account_hash),
            )
            cursor = connection.execute(
                "INSERT INTO trading_sessions (account_hash, label, started_at)"
                " VALUES (?, ?, ?)",
                (account_hash, label.strip(), opened_at),
            )
            session_id = int(cursor.lastrowid)
        return TradingSession(session_id, account_hash, label.strip(), opened_at, None)

    def end_trading_session(self, account_hash: str | None = None) -> TradingSession | None:
        running = self.active_trading_session(account_hash)
        if running is None:
            return None
        ended_at = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE trading_sessions SET ended_at = ? WHERE session_id = ?",
                (ended_at, running.session_id),
            )
        return replace(running, ended_at=ended_at)

    def active_trading_session(self, account_hash: str | None = None) -> TradingSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trading_sessions"
                " WHERE ended_at IS NULL AND (account_hash IS ? OR account_hash IS NULL)"
                " ORDER BY started_at DESC, session_id DESC LIMIT 1",
                (account_hash,),
            ).fetchone()
        return _trading_session(row) if row is not None else None

    def list_trading_sessions(
        self, account_hash: str | None = None, limit: int = 50
    ) -> list[TradingSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trading_sessions"
                " WHERE account_hash IS ? OR account_hash IS NULL OR ? IS NULL"
                " ORDER BY started_at DESC, session_id DESC LIMIT ?",
                (account_hash, account_hash, max(1, limit)),
            ).fetchall()
        return [_trading_session(row) for row in rows]

    def _new_position_status(
        self, account_hash: str | None, occurred_at: str | None = None
    ) -> str:
        if self.flip_tracking_mode() == "always":
            return "Pending buy"
        return "Pending buy" if self._session_covers(account_hash, occurred_at) else "Supplies"

    def _session_covers(self, account_hash: str | None, occurred_at: str | None) -> bool:
        if occurred_at is None:
            return self.active_trading_session(account_hash) is not None
        try:
            moment = _instant(occurred_at)
        except ValueError:
            return self.active_trading_session(account_hash) is not None
        for session in self.list_trading_sessions(account_hash, limit=200):
            if _instant(session.started_at) > moment:
                continue
            if session.ended_at is None or _instant(session.ended_at) >= moment:
                return True
        return False

    def add(self, item_name: str, quantity: int, buy_price: int, sell_price: int) -> int:
        with self._connect() as connection:
            recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                INSERT INTO trades (recorded_at, item_name, quantity, buy_price, sell_price,
                    sync_uid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    item_name.strip(),
                    quantity,
                    buy_price,
                    sell_price,
                    uuid.uuid4().hex,
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def trade_sync_uid(self, trade_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sync_uid FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return str(row["sync_uid"]) if row and row["sync_uid"] else None

    def list_all(self) -> list[TradeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trades WHERE deleted_at IS NULL"
                " ORDER BY recorded_at DESC, trade_id DESC"
            ).fetchall()
        return [
            TradeRecord(
                trade_id=int(row["trade_id"]),
                recorded_at=str(row["recorded_at"]),
                item_name=str(row["item_name"]),
                quantity=int(row["quantity"]),
                buy_price=int(row["buy_price"]),
                sell_price=int(row["sell_price"]),
                sync_uid=row["sync_uid"],
            )
            for row in rows
        ]

    def delete(self, trade_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE trades SET deleted_at = ?, updated_at = ? WHERE trade_id = ?",
                (_now(), _now(), trade_id),
            )

    def track(
        self,
        item_id: int | None,
        item_name: str,
        quantity: int,
        target_buy: int,
        target_sell: int,
        strategy: str = "Balanced (1–4h)",
        account_hash: str | None = None,
    ) -> int:
        with self._connect() as connection:
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                INSERT INTO tracked_trades (
                    created_at, item_id, item_name, quantity, target_buy, target_sell, status,
                    strategy, current_buy_suggestion, current_sell_suggestion,
                    suggestion_reviewed_at, account_hash, sync_uid, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending buy', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    item_id,
                    item_name,
                    quantity,
                    target_buy,
                    target_sell,
                    strategy,
                    target_buy,
                    target_sell,
                    created_at,
                    account_hash,
                    uuid.uuid4().hex,
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_tracked(
        self, account_hash: str | None = None, item_id: int | None = None
    ) -> list[TrackedTrade]:
        clauses = ["deleted_at IS NULL"]
        parameters: list[object] = []
        if account_hash is not None:
            clauses.append("(account_hash = ? OR account_hash IS NULL)")
            parameters.append(account_hash)
        if item_id is not None:
            clauses.append("item_id = ?")
            parameters.append(item_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tracked_trades WHERE {' AND '.join(clauses)}"
                " ORDER BY created_at DESC, position_id DESC",
                tuple(parameters),
            ).fetchall()
            position_ids = [int(row["position_id"]) for row in rows]
            fill_rows = _fills_for(connection, "tracked_sale_fills", position_ids)
            buy_fill_rows = _fills_for(connection, "tracked_buy_fills", position_ids)
        fills_by_position: dict[int, list[SaleFill]] = {}
        for row in fill_rows:
            position_id = int(row["position_id"])
            fills_by_position.setdefault(position_id, []).append(
                SaleFill(
                    fill_id=int(row["fill_id"]),
                    position_id=position_id,
                    quantity=int(row["quantity"]),
                    sell_price=int(row["sell_price"]),
                )
            )
        buy_fills_by_position: dict[int, list[BuyFill]] = {}
        for row in buy_fill_rows:
            position_id = int(row["position_id"])
            buy_fills_by_position.setdefault(position_id, []).append(
                BuyFill(
                    fill_id=int(row["fill_id"]),
                    position_id=position_id,
                    quantity=int(row["quantity"]),
                    buy_price=int(row["buy_price"]),
                )
            )
        return [
            TrackedTrade(
                position_id=int(row["position_id"]),
                created_at=str(row["created_at"]),
                item_id=int(row["item_id"]) if row["item_id"] is not None else None,
                item_name=str(row["item_name"]),
                quantity=int(row["quantity"]),
                target_buy=int(row["target_buy"]),
                target_sell=int(row["target_sell"]),
                actual_buy=int(row["actual_buy"]) if row["actual_buy"] is not None else None,
                actual_sell=int(row["actual_sell"]) if row["actual_sell"] is not None else None,
                status=str(row["status"]),
                sale_fills=tuple(fills_by_position.get(int(row["position_id"]), [])),
                buy_fills=tuple(buy_fills_by_position.get(int(row["position_id"]), [])),
                strategy=str(row["strategy"]),
                current_buy_suggestion=(
                    int(row["current_buy_suggestion"])
                    if row["current_buy_suggestion"] is not None
                    else None
                ),
                current_sell_suggestion=(
                    int(row["current_sell_suggestion"])
                    if row["current_sell_suggestion"] is not None
                    else None
                ),
                suggestion_reviewed_at=(
                    str(row["suggestion_reviewed_at"])
                    if row["suggestion_reviewed_at"] is not None
                    else None
                ),
                completed_at=(
                    str(row["completed_at"]) if row["completed_at"] is not None else None
                ),
                listed_sell_price=(
                    int(row["listed_sell_price"]) if row["listed_sell_price"] is not None else None
                ),
                account_hash=(
                    str(row["account_hash"]) if row["account_hash"] is not None else None
                ),
            )
            for row in rows
        ]

    def update_tracked(
        self,
        position_id: int,
        status: str,
        actual_buy: int | None,
        actual_sell: int | None,
        sale_fills: list[tuple[int, int]] | None = None,
        buy_fills: list[tuple[int, int]] | None = None,
        quantity: int | None = None,
    ) -> None:
        with self._connect() as connection:
            trade_row = connection.execute(
                "SELECT quantity, status, completed_at FROM tracked_trades WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            if trade_row is None:
                return
            if quantity is not None and quantity <= 0:
                raise ValueError("Quantity must be positive")
            effective_quantity = quantity if quantity is not None else int(trade_row["quantity"])

            if buy_fills is not None:
                if any(fill_quantity <= 0 or price < 0 for fill_quantity, price in buy_fills):
                    raise ValueError("Buy fill quantity and price must be positive")
                bought_quantity = sum(fill_quantity for fill_quantity, _price in buy_fills)
                if bought_quantity > effective_quantity:
                    raise ValueError("Buy fills exceed the tracked quantity")
                if status == "Completed" and bought_quantity != effective_quantity:
                    raise ValueError(
                        "A completed trade must account for the full quantity of buy fills"
                    )
                actual_buy = (
                    round(
                        sum(fill_quantity * price for fill_quantity, price in buy_fills)
                        / bought_quantity
                    )
                    if bought_quantity
                    else None
                )

            if sale_fills is None and actual_sell is not None and status == "Completed":
                sale_fills = [(effective_quantity, actual_sell)]
            if sale_fills is not None:
                if any(fill_quantity <= 0 or price < 0 for fill_quantity, price in sale_fills):
                    raise ValueError("Sale fill quantity and price must be positive")
                sold_quantity = sum(fill_quantity for fill_quantity, _price in sale_fills)
                if sold_quantity > effective_quantity:
                    raise ValueError("Sale fills exceed the tracked quantity")
                if status == "Completed" and sold_quantity != effective_quantity:
                    raise ValueError("A completed trade must account for the full quantity")
                actual_sell = (
                    round(
                        sum(fill_quantity * price for fill_quantity, price in sale_fills)
                        / sold_quantity
                    )
                    if sold_quantity
                    else None
                )

            completed_at = self._completion_time(
                status, str(trade_row["status"]), trade_row["completed_at"]
            )
            connection.execute(
                """
                UPDATE tracked_trades
                SET status = ?, actual_buy = ?, actual_sell = ?, quantity = ?, completed_at = ?
                WHERE position_id = ?
                """,
                (status, actual_buy, actual_sell, effective_quantity, completed_at, position_id),
            )
            if sale_fills is not None:
                connection.execute(
                    "DELETE FROM tracked_sale_fills WHERE position_id = ?", (position_id,)
                )
                connection.executemany(
                    """
                    INSERT INTO tracked_sale_fills (position_id, quantity, sell_price)
                    VALUES (?, ?, ?)
                    """,
                    [(position_id, fill_quantity, price) for fill_quantity, price in sale_fills],
                )
            if buy_fills is not None:
                connection.execute(
                    "DELETE FROM tracked_buy_fills WHERE position_id = ?", (position_id,)
                )
                connection.executemany(
                    """
                    INSERT INTO tracked_buy_fills (position_id, quantity, buy_price)
                    VALUES (?, ?, ?)
                    """,
                    [(position_id, fill_quantity, price) for fill_quantity, price in buy_fills],
                )

    def _completion_time(
        self, status: str, previous_status: str, previous_completed_at: str | None
    ) -> str | None:
        if status not in self._TERMINAL_STATUSES:
            return None
        if previous_status in self._TERMINAL_STATUSES and previous_completed_at:
            return str(previous_completed_at)
        return datetime.now(UTC).isoformat(timespec="seconds")

    def apply_synced_ge_fill(
        self,
        item_id: int,
        item_name: str,
        side: str,
        quantity: int,
        unit_price: int,
        total_quantity: int | None = None,
        suggested_sell_price: int | None = None,
        account_hash: str | None = None,
        occurred_at: str | None = None,
    ) -> int | None:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        eligible_statuses = (
            ("Pending buy", "Supplies")
            if side == "buy"
            else ("Bought", "Listed for sale", "Partially sold", "Supplies")
        )
        tracked = self.list_tracked(account_hash, item_id)
        candidates = sorted(
            (trade for trade in tracked if trade.status in eligible_statuses),
            key=lambda trade: trade.created_at,
        )
        if side == "sell":
            candidates += sorted(
                (
                    trade
                    for trade in tracked
                    if trade.status == "Pending buy" and trade.bought_quantity > 0
                ),
                key=lambda trade: trade.created_at,
            )
        for trade in candidates:
            if side == "buy":
                remaining = trade.quantity - trade.bought_quantity
                if remaining < quantity:
                    continue
                new_buy_fills = [(fill.quantity, fill.buy_price) for fill in trade.buy_fills]
                new_buy_fills.append((quantity, unit_price))
                new_status = (
                    trade.status
                    if trade.status == "Supplies"
                    else "Bought"
                    if trade.bought_quantity + quantity == trade.quantity
                    else "Pending buy"
                )
                self.update_tracked(
                    trade.position_id, new_status, None, trade.actual_sell, None, new_buy_fills
                )
            else:
                part_bought = trade.status == "Pending buy"
                stock = trade.bought_quantity if part_bought else trade.quantity
                remaining = stock - trade.sold_quantity
                if remaining < quantity:
                    continue
                new_sale_fills = [(fill.quantity, fill.sell_price) for fill in trade.sale_fills]
                new_sale_fills.append((quantity, unit_price))
                new_status = (
                    trade.status
                    if trade.status == "Supplies"
                    else "Completed"
                    if trade.sold_quantity + quantity == stock
                    else "Partially sold"
                )
                self.update_tracked(
                    trade.position_id,
                    new_status,
                    trade.actual_buy,
                    None,
                    new_sale_fills,
                    quantity=stock if part_bought else None,
                )
            return trade.position_id
        if side != "buy" or not total_quantity or total_quantity < quantity:
            return None
        position_id = self.track(
            item_id,
            item_name,
            total_quantity,
            unit_price,
            unit_price,
            account_hash=account_hash,
        )
        opened_as = self._new_position_status(account_hash, occurred_at)
        if opened_as == "Supplies":
            self.update_tracked(
                position_id, "Supplies", None, None, None, [(quantity, unit_price)]
            )
            return position_id
        status = "Bought" if quantity == total_quantity else "Pending buy"
        self.update_tracked(position_id, status, None, None, None, [(quantity, unit_price)])
        if suggested_sell_price is not None and suggested_sell_price > unit_price:
            self.review_suggestion(position_id, unit_price, suggested_sell_price)
        return position_id

    def apply_offer_opened(
        self,
        item_id: int,
        item_name: str,
        side: str,
        total_quantity: int,
        offer_price: int,
        suggested_sell_price: int | None = None,
        restored: bool = False,
        account_hash: str | None = None,
        occurred_at: str | None = None,
    ) -> int | None:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        by_age = sorted(
            self.list_tracked(account_hash, item_id), key=lambda trade: trade.created_at
        )
        if side == "buy":
            untouched_plan = next(
                (
                    trade
                    for trade in by_age
                    if trade.status == "Pending buy"
                    and trade.bought_quantity == 0
                    and trade.target_sell > trade.target_buy
                ),
                None,
            )
            already_tracked = self._offer_already_tracked(
                by_age, total_quantity, offer_price, restored
            )
            planned = (
                already_tracked or untouched_plan if restored else untouched_plan or already_tracked
            )
            if planned is None:
                position_id = self.track(
                    item_id,
                    item_name,
                    total_quantity,
                    offer_price,
                    offer_price,
                    account_hash=account_hash,
                )
                if self._new_position_status(account_hash, occurred_at) == "Supplies":
                    self.update_tracked(position_id, "Supplies", None, None)
                    return position_id
                if suggested_sell_price is not None and suggested_sell_price > offer_price:
                    self.review_suggestion(position_id, offer_price, suggested_sell_price)
                return position_id
            if total_quantity > planned.quantity:
                self.update_tracked(
                    planned.position_id,
                    planned.status,
                    planned.actual_buy,
                    planned.actual_sell,
                    quantity=total_quantity,
                )
            return planned.position_id
        candidate = next((trade for trade in by_age if trade.status == "Bought"), None)
        if candidate is None and restored:
            already_listed = next(
                (trade for trade in by_age if trade.status == "Listed for sale"),
                None,
            )
            if already_listed is not None:
                self.record_listed_price(already_listed.position_id, offer_price)
                return already_listed.position_id
        if candidate is None:
            candidate = next(
                (
                    trade
                    for trade in by_age
                    if trade.status == "Pending buy" and trade.bought_quantity > 0
                ),
                None,
            )
            if candidate is None:
                part_sold = next(
                    (trade for trade in by_age if trade.status == "Partially sold"), None
                )
                if part_sold is None:
                    return None
                self.record_listed_price(part_sold.position_id, offer_price)
                return part_sold.position_id
        self.update_tracked(
            candidate.position_id,
            "Listed for sale",
            candidate.actual_buy,
            candidate.actual_sell,
            quantity=(candidate.bought_quantity if candidate.status == "Pending buy" else None),
        )
        self.record_listed_price(candidate.position_id, offer_price)
        return candidate.position_id

    @staticmethod
    def _offer_already_tracked(
        by_age: list[TrackedTrade],
        total_quantity: int,
        offer_price: int,
        restored: bool,
    ) -> TrackedTrade | None:
        in_flight = [
            trade
            for trade in by_age
            if trade.status in ("Pending buy", "Supplies")
            and trade.bought_quantity < trade.quantity
        ]
        if not in_flight:
            return None
        if restored:
            return min(
                in_flight,
                key=lambda trade: (trade.quantity != total_quantity, trade.created_at),
            )
        return next(
            (
                trade
                for trade in in_flight
                if trade.bought_quantity > 0
                and trade.quantity == total_quantity
                and trade.target_buy == offer_price
            ),
            None,
        )

    def apply_offer_cancelled(
        self,
        item_id: int,
        side: str,
        total_quantity: int,
        offer_price: int,
        account_hash: str | None = None,
    ) -> int | None:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        by_age = sorted(
            self.list_tracked(account_hash, item_id), key=lambda trade: trade.created_at
        )
        if side == "sell":
            candidate = next(
                (trade for trade in by_age if trade.status == "Listed for sale"), None
            )
            if candidate is None:
                return None
            self.update_tracked(
                candidate.position_id, "Bought", candidate.actual_buy, candidate.actual_sell
            )
            return candidate.position_id
        candidate = next(
            (
                trade
                for trade in by_age
                if trade.status in ("Pending buy", "Supplies")
                and trade.quantity == total_quantity
                and trade.target_buy == offer_price
            ),
            None,
        )
        if candidate is None:
            return None
        if candidate.bought_quantity == 0:
            if candidate.target_sell > candidate.target_buy:
                return None
            self.delete_tracked(candidate.position_id)
            return candidate.position_id
        self.update_tracked(
            candidate.position_id,
            "Supplies" if candidate.status == "Supplies" else "Bought",
            None,
            candidate.actual_sell,
            None,
            [(fill.quantity, fill.buy_price) for fill in candidate.buy_fills],
            quantity=candidate.bought_quantity,
        )
        return candidate.position_id

    def retag_positions(
        self,
        position_ids: Sequence[int],
        *,
        status: str | None = None,
        strategy: str | None = None,
        account_hash: str | None | _Unchanged = UNCHANGED,
    ) -> RetagResult:
        if status is not None and status not in self._ALL_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        wanted = list(dict.fromkeys(position_ids))
        if not wanted or (status is None and strategy is None and account_hash is UNCHANGED):
            return RetagResult(updated=0, skipped=())

        by_id = {trade.position_id: trade for trade in self.list_tracked()}
        skipped: list[int] = []
        changed: list[int] = []
        for position_id in wanted:
            trade = by_id.get(position_id)
            if trade is None:
                continue
            if status == "Completed" and not self._can_complete(trade):
                skipped.append(position_id)
                continue
            changed.append(position_id)

        with self._connect() as connection:
            for position_id in changed:
                trade = by_id[position_id]
                assignments = ["updated_at = ?"]
                values: list[object] = [_now()]
                if status is not None:
                    assignments.append("status = ?")
                    values.append(status)
                    assignments.append("completed_at = ?")
                    values.append(self._completion_time(status, trade.status, trade.completed_at))
                if strategy is not None:
                    assignments.append("strategy = ?")
                    values.append(strategy)
                if account_hash is not UNCHANGED:
                    assignments.append("account_hash = ?")
                    values.append(account_hash)
                values.append(position_id)
                connection.execute(
                    f"UPDATE tracked_trades SET {', '.join(assignments)} WHERE position_id = ?",
                    values,
                )
        return RetagResult(updated=len(changed), skipped=tuple(skipped))

    @staticmethod
    def _can_complete(trade: TrackedTrade) -> bool:
        return trade.bought_quantity == trade.quantity and trade.sold_quantity == trade.quantity

    def delete_tracked(self, position_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_trades SET deleted_at = ?, updated_at = ? WHERE position_id = ?",
                (_now(), _now(), position_id),
            )

    def record_listed_price(self, position_id: int, sell_price: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_trades SET listed_sell_price = ? WHERE position_id = ?",
                (sell_price, position_id),
            )

    def review_suggestion(
        self,
        position_id: int,
        buy_price: int,
        sell_price: int,
        reviewed_at: str | None = None,
    ) -> None:
        reviewed_at = reviewed_at or datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tracked_trades
                SET current_buy_suggestion = ?, current_sell_suggestion = ?,
                    suggestion_reviewed_at = ?
                WHERE position_id = ?
                """,
                (buy_price, sell_price, reviewed_at, position_id),
            )

    def add_synced_trade(self, trade: SyncedTrade) -> bool:
        return self.add_synced_trades([trade])[0]

    def claim_offer_event(self, event_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO applied_offer_events (event_id, applied_at) VALUES (?, ?)",
                (event_id, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            return cursor.rowcount > 0

    def add_synced_trades(self, trades: list[SyncedTrade]) -> list[bool]:
        if not trades:
            return []
        imported_at = datetime.now(UTC).isoformat(timespec="seconds")
        results: list[bool] = []
        with self._connect() as connection:
            for trade in trades:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO synced_trades (
                        event_id, occurred_at, imported_at, event_type, account_hash,
                        account_name, counterparty, direction, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.event_id,
                        trade.occurred_at,
                        imported_at,
                        trade.event_type,
                        trade.account_hash,
                        trade.account_name,
                        trade.counterparty,
                        trade.direction,
                        json.dumps(trade.metadata, separators=(",", ":"), sort_keys=True),
                    ),
                )
                if cursor.rowcount == 0:
                    results.append(False)
                    continue
                connection.executemany(
                    """
                    INSERT INTO synced_trade_items (
                        event_id, flow, item_id, item_name, quantity, unit_value
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            trade.event_id,
                            item.flow,
                            item.item_id,
                            item.item_name,
                            item.quantity,
                            item.unit_value,
                        )
                        for item in trade.items
                    ],
                )
                results.append(True)
        return results

    def list_synced_trades(
        self,
        event_type: str | None = None,
        *,
        since: datetime | None = None,
        account_hash: str | None = None,
    ) -> list[SyncedTrade]:
        conditions: list[str] = []
        parameters: list[str] = []
        if event_type is not None:
            conditions.append("event_type = ?")
            parameters.append(event_type)
        if account_hash is not None:
            conditions.append("account_hash = ?")
            parameters.append(account_hash)
        if since is not None:
            conditions.append("occurred_at >= ?")
            parameters.append(
                (since - NOT_BEFORE_SAFETY_MARGIN).astimezone(UTC).isoformat(timespec="seconds")
            )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM synced_trades{where} ORDER BY occurred_at DESC, event_id DESC",
                parameters,
            ).fetchall()
            item_rows = connection.execute(
                f"""
                SELECT * FROM synced_trade_items
                WHERE event_id IN (SELECT event_id FROM synced_trades{where})
                ORDER BY item_row_id ASC
                """,
                parameters,
            ).fetchall()
        items_by_event: dict[str, list[SyncedItem]] = {}
        for row in item_rows:
            items_by_event.setdefault(str(row["event_id"]), []).append(
                SyncedItem(
                    flow=str(row["flow"]),
                    item_id=int(row["item_id"]),
                    item_name=str(row["item_name"]),
                    quantity=int(row["quantity"]),
                    unit_value=int(row["unit_value"]),
                )
            )
        return [
            SyncedTrade(
                event_id=str(row["event_id"]),
                occurred_at=str(row["occurred_at"]),
                event_type=str(row["event_type"]),
                account_hash=str(row["account_hash"]),
                account_name=str(row["account_name"]),
                counterparty=str(row["counterparty"]) if row["counterparty"] else None,
                direction=str(row["direction"]),
                metadata=json.loads(str(row["metadata_json"])),
                items=tuple(items_by_event.get(str(row["event_id"]), [])),
            )
            for row in rows
        ]

    def get_synced_trade(self, event_id: str) -> SyncedTrade | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM synced_trades WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return None
            item_rows = connection.execute(
                "SELECT * FROM synced_trade_items WHERE event_id = ? ORDER BY item_row_id ASC",
                (event_id,),
            ).fetchall()
        items = tuple(
            SyncedItem(
                flow=str(item_row["flow"]),
                item_id=int(item_row["item_id"]),
                item_name=str(item_row["item_name"]),
                quantity=int(item_row["quantity"]),
                unit_value=int(item_row["unit_value"]),
            )
            for item_row in item_rows
        )
        return SyncedTrade(
            event_id=str(row["event_id"]),
            occurred_at=str(row["occurred_at"]),
            event_type=str(row["event_type"]),
            account_hash=str(row["account_hash"]),
            account_name=str(row["account_name"]),
            counterparty=str(row["counterparty"]) if row["counterparty"] else None,
            direction=str(row["direction"]),
            metadata=json.loads(str(row["metadata_json"])),
            items=items,
        )

    def delete_synced_trade(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM synced_trades WHERE event_id = ?", (event_id,))

    def add_npc_loot_event(self, event: NpcLootRecord) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO npc_loot_events (
                    event_id, occurred_at, imported_at, account_hash, account_name, npc_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.occurred_at,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    event.account_hash,
                    event.account_name,
                    event.npc_name,
                ),
            )
            if cursor.rowcount == 0:
                return False
            connection.executemany(
                """
                INSERT INTO npc_loot_items (
                    event_id, item_id, item_name, quantity, unit_value
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (event.event_id, item.item_id, item.item_name, item.quantity, item.unit_value)
                    for item in event.items
                ],
            )
            return True

    def list_npc_loot_events(
        self,
        *,
        since: datetime | None = None,
        account_hash: str | None = None,
        npc_name: str | None = None,
    ) -> list[NpcLootRecord]:
        conditions: list[str] = []
        parameters: list[str] = []
        if account_hash is not None:
            conditions.append("account_hash = ?")
            parameters.append(account_hash)
        if npc_name is not None:
            conditions.append("npc_name = ?")
            parameters.append(npc_name)
        if since is not None:
            conditions.append("occurred_at >= ?")
            parameters.append(
                (since - NOT_BEFORE_SAFETY_MARGIN).astimezone(UTC).isoformat(timespec="seconds")
            )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM npc_loot_events{where} ORDER BY occurred_at DESC, event_id DESC",
                parameters,
            ).fetchall()
            item_rows = connection.execute(
                f"""
                SELECT * FROM npc_loot_items
                WHERE event_id IN (SELECT event_id FROM npc_loot_events{where})
                ORDER BY item_row_id ASC
                """,
                parameters,
            ).fetchall()
        items_by_event: dict[str, list[LoadoutItem]] = {}
        for row in item_rows:
            items_by_event.setdefault(str(row["event_id"]), []).append(
                LoadoutItem(
                    item_id=int(row["item_id"]),
                    item_name=str(row["item_name"]),
                    quantity=int(row["quantity"]),
                    unit_value=int(row["unit_value"]),
                )
            )
        return [
            NpcLootRecord(
                event_id=str(row["event_id"]),
                occurred_at=str(row["occurred_at"]),
                account_hash=str(row["account_hash"]),
                account_name=str(row["account_name"]),
                npc_name=str(row["npc_name"]),
                items=tuple(items_by_event.get(str(row["event_id"]), [])),
            )
            for row in rows
        ]

    def delete_npc_loot_event(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM npc_loot_events WHERE event_id = ?", (event_id,))

    def add_player_death_event(self, event: PlayerDeathRecord) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO player_death_events (
                    event_id, occurred_at, imported_at, account_hash, account_name, skulled
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.occurred_at,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    event.account_hash,
                    event.account_name,
                    1 if event.skulled else 0,
                ),
            )
            if cursor.rowcount == 0:
                return False
            connection.executemany(
                """
                INSERT INTO player_death_items (
                    event_id, flow, item_id, item_name, quantity, unit_value
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event.event_id,
                        "equipment",
                        item.item_id,
                        item.item_name,
                        item.quantity,
                        item.unit_value,
                    )
                    for item in event.equipment
                ]
                + [
                    (
                        event.event_id,
                        "inventory",
                        item.item_id,
                        item.item_name,
                        item.quantity,
                        item.unit_value,
                    )
                    for item in event.inventory
                ],
            )
            return True

    def list_player_death_events(
        self,
        *,
        since: datetime | None = None,
        account_hash: str | None = None,
    ) -> list[PlayerDeathRecord]:
        conditions: list[str] = []
        parameters: list[str] = []
        if account_hash is not None:
            conditions.append("account_hash = ?")
            parameters.append(account_hash)
        if since is not None:
            conditions.append("occurred_at >= ?")
            parameters.append(
                (since - NOT_BEFORE_SAFETY_MARGIN).astimezone(UTC).isoformat(timespec="seconds")
            )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM player_death_events{where} "
                "ORDER BY occurred_at DESC, event_id DESC",
                parameters,
            ).fetchall()
            item_rows = connection.execute(
                f"""
                SELECT * FROM player_death_items
                WHERE event_id IN (SELECT event_id FROM player_death_events{where})
                ORDER BY item_row_id ASC
                """,
                parameters,
            ).fetchall()
        equipment_by_event: dict[str, list[LoadoutItem]] = {}
        inventory_by_event: dict[str, list[LoadoutItem]] = {}
        for row in item_rows:
            bucket = equipment_by_event if row["flow"] == "equipment" else inventory_by_event
            bucket.setdefault(str(row["event_id"]), []).append(
                LoadoutItem(
                    item_id=int(row["item_id"]),
                    item_name=str(row["item_name"]),
                    quantity=int(row["quantity"]),
                    unit_value=int(row["unit_value"]),
                )
            )
        return [
            PlayerDeathRecord(
                event_id=str(row["event_id"]),
                occurred_at=str(row["occurred_at"]),
                account_hash=str(row["account_hash"]),
                account_name=str(row["account_name"]),
                skulled=bool(row["skulled"]),
                equipment=tuple(equipment_by_event.get(str(row["event_id"]), [])),
                inventory=tuple(inventory_by_event.get(str(row["event_id"]), [])),
            )
            for row in rows
        ]

    def delete_player_death_event(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM player_death_events WHERE event_id = ?", (event_id,))

    def save_loadout_snapshot(self, snapshot: LoadoutSnapshot) -> None:
        def dump(items: tuple[LoadoutItem, ...]) -> str:
            return json.dumps(
                [
                    {
                        "item_id": item.item_id,
                        "item_name": item.item_name,
                        "quantity": item.quantity,
                        "unit_value": item.unit_value,
                    }
                    for item in items
                ],
                separators=(",", ":"),
            )

        equipment_json = dump(snapshot.equipment)
        inventory_json = dump(snapshot.inventory)
        bank_json = dump(snapshot.bank)
        skills_json = json.dumps(snapshot.skills, separators=(",", ":"))
        xp_json = json.dumps(snapshot.xp, separators=(",", ":"))

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO loadout_snapshots (
                    account_hash, account_name, captured_at, equipment_json, inventory_json,
                    bank_json, skills_json, xp_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_hash) DO UPDATE SET
                    account_name = excluded.account_name,
                    captured_at = excluded.captured_at,
                    equipment_json = excluded.equipment_json,
                    inventory_json = excluded.inventory_json,
                    bank_json = excluded.bank_json,
                    skills_json = excluded.skills_json,
                    xp_json = excluded.xp_json
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    equipment_json,
                    inventory_json,
                    bank_json,
                    skills_json,
                    xp_json,
                ),
            )
            connection.execute(
                """
                INSERT INTO loadout_snapshot_history (
                    account_hash, account_name, captured_at, equipment_json, inventory_json,
                    bank_json, skills_json, xp_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_hash, captured_at) DO UPDATE SET
                    account_name = excluded.account_name,
                    equipment_json = excluded.equipment_json,
                    inventory_json = excluded.inventory_json,
                    bank_json = excluded.bank_json,
                    skills_json = excluded.skills_json,
                    xp_json = excluded.xp_json
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    equipment_json,
                    inventory_json,
                    bank_json,
                    skills_json,
                    xp_json,
                ),
            )
            self._prune_loadout_snapshot_history(connection, snapshot.account_hash)
            connection.execute(
                """
                INSERT INTO net_worth_history (
                    account_hash, account_name, captured_at, total_value
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    snapshot.total_value,
                ),
            )
            connection.execute(
                """
                DELETE FROM net_worth_history
                WHERE account_hash = ? AND entry_id NOT IN (
                    SELECT entry_id FROM net_worth_history
                    WHERE account_hash = ?
                    ORDER BY captured_at DESC, entry_id DESC
                    LIMIT ?
                )
                """,
                (snapshot.account_hash, snapshot.account_hash, MAX_NET_WORTH_HISTORY_PER_ACCOUNT),
            )
            connection.execute(
                """
                INSERT INTO skills_history (
                    account_hash, account_name, captured_at, skills_json, xp_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    skills_json,
                    xp_json,
                ),
            )
            connection.execute(
                """
                DELETE FROM skills_history
                WHERE account_hash = ? AND entry_id NOT IN (
                    SELECT entry_id FROM skills_history
                    WHERE account_hash = ?
                    ORDER BY captured_at DESC, entry_id DESC
                    LIMIT ?
                )
                """,
                (snapshot.account_hash, snapshot.account_hash, MAX_SKILLS_HISTORY_PER_ACCOUNT),
            )

    @staticmethod
    def _prune_loadout_snapshot_history(
        connection: sqlite3.Connection, account_hash: str
    ) -> None:
        rows = connection.execute(
            "SELECT entry_id, captured_at FROM loadout_snapshot_history WHERE account_hash = ?",
            (account_hash,),
        ).fetchall()
        now = datetime.now(UTC)
        keep_everything_after = now - timedelta(days=SNAPSHOT_HISTORY_FULL_DAYS)
        keep_daily_after = now - timedelta(days=SNAPSHOT_HISTORY_DAILY_DAYS)
        newest_in_bucket: dict[tuple[object, ...], tuple[datetime, int]] = {}
        doomed: list[int] = []
        for row in rows:
            moment = _as_moment(row["captured_at"])
            if moment >= keep_everything_after:
                continue
            if moment >= keep_daily_after:
                bucket: tuple[object, ...] = ("day", moment.date().isoformat())
            else:
                year, week, _ = moment.isocalendar()
                bucket = ("week", year, week)
            candidate = (moment, int(row["entry_id"]))
            previous = newest_in_bucket.get(bucket)
            if previous is None:
                newest_in_bucket[bucket] = candidate
            elif candidate > previous:
                newest_in_bucket[bucket] = candidate
                doomed.append(previous[1])
            else:
                doomed.append(candidate[1])
        if doomed:
            connection.executemany(
                "DELETE FROM loadout_snapshot_history WHERE entry_id = ?",
                [(entry_id,) for entry_id in doomed],
            )

    def list_loadout_snapshots(
        self,
        account_hash: str | None = None,
        *,
        since: str | None = None,
        until: str | None = None,
    ) -> list[LoadoutSnapshot]:
        lower = _as_moment(since) if since else None
        upper = _as_moment(until) if until else None
        with self._connect() as connection:
            if account_hash is None:
                index = connection.execute(
                    "SELECT entry_id, captured_at FROM loadout_snapshot_history"
                ).fetchall()
            else:
                index = connection.execute(
                    "SELECT entry_id, captured_at FROM loadout_snapshot_history"
                    " WHERE account_hash = ?",
                    (account_hash,),
                ).fetchall()
            wanted = sorted(
                (moment, entry_id)
                for moment, entry_id in (
                    (_as_moment(row["captured_at"]), int(row["entry_id"])) for row in index
                )
                if (lower is None or moment >= lower) and (upper is None or moment <= upper)
            )
            by_id: dict[int, sqlite3.Row] = {}
            for start in range(0, len(wanted), MAX_SQL_PARAMETERS):
                chunk = [entry_id for _, entry_id in wanted[start : start + MAX_SQL_PARAMETERS]]
                placeholders = ",".join("?" * len(chunk))
                by_id.update(
                    (int(row["entry_id"]), row)
                    for row in connection.execute(
                        "SELECT * FROM loadout_snapshot_history "
                        f"WHERE entry_id IN ({placeholders})",
                        chunk,
                    )
                )
        return [self._row_to_loadout_snapshot(by_id[entry_id]) for _, entry_id in wanted]

    def list_net_worth_history(self, account_hash: str | None = None) -> list[NetWorthPoint]:
        with self._connect() as connection:
            if account_hash is None:
                rows = connection.execute(
                    "SELECT * FROM net_worth_history ORDER BY captured_at ASC, entry_id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM net_worth_history
                    WHERE account_hash = ?
                    ORDER BY captured_at ASC, entry_id ASC
                    """,
                    (account_hash,),
                ).fetchall()
        return [
            NetWorthPoint(
                account_hash=str(row["account_hash"]),
                account_name=str(row["account_name"]),
                captured_at=str(row["captured_at"]),
                total_value=int(row["total_value"]),
            )
            for row in rows
        ]

    def list_completed_quests(self, account_hash: str) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT quest_name FROM quest_progress WHERE account_hash = ?",
                (account_hash,),
            ).fetchall()
        return frozenset(str(row["quest_name"]) for row in rows)

    def set_quests_completed(
        self, account_hash: str, quest_names: Sequence[str], completed: bool
    ) -> None:
        names = list(dict.fromkeys(quest_names))
        if not names:
            return
        with self._connect() as connection:
            if completed:
                connection.executemany(
                    "INSERT OR IGNORE INTO quest_progress (account_hash, quest_name, completed_at)"
                    " VALUES (?, ?, ?)",
                    [(account_hash, name, _now()) for name in names],
                )
            else:
                connection.executemany(
                    "DELETE FROM quest_progress WHERE account_hash = ? AND quest_name = ?",
                    [(account_hash, name) for name in names],
                )

    def list_skills_history(self, account_hash: str | None = None) -> list[SkillsPoint]:
        with self._connect() as connection:
            if account_hash is None:
                rows = connection.execute(
                    "SELECT * FROM skills_history ORDER BY captured_at ASC, entry_id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM skills_history
                    WHERE account_hash = ?
                    ORDER BY captured_at ASC, entry_id ASC
                    """,
                    (account_hash,),
                ).fetchall()
        return [
            SkillsPoint(
                account_hash=str(row["account_hash"]),
                account_name=str(row["account_name"]),
                captured_at=str(row["captured_at"]),
                skills=json.loads(str(row["skills_json"])),
                xp={str(k): int(v) for k, v in json.loads(str(row["xp_json"])).items()},
            )
            for row in rows
        ]

    def session_summary(self, account_hash: str) -> SessionSummary | None:
        loot = self.list_npc_loot_events(account_hash=account_hash)
        deaths = self.list_player_death_events(account_hash=account_hash)
        trades = self.list_synced_trades(event_type="player_trade", account_hash=account_hash)
        skills_history = self.list_skills_history(account_hash)
        net_worth_history = self.list_net_worth_history(account_hash)

        skill_moments = _changed_snapshot_moments(
            skills_history,
            lambda before, after: before.skills != after.skills or before.xp != after.xp,
        )
        net_worth_moments = _changed_snapshot_moments(
            net_worth_history, lambda before, after: before.total_value != after.total_value
        )

        moments = sorted(
            [_as_moment(event.occurred_at) for event in (*loot, *deaths, *trades)]
            + skill_moments
            + net_worth_moments
        )
        if not moments:
            return None

        threshold = timedelta(minutes=SESSION_GAP_MINUTES)
        end_instant = start_instant = moments[-1]
        for moment in reversed(moments[:-1]):
            if start_instant - moment > threshold:
                break
            start_instant = moment

        def in_window(occurred_at: str) -> bool:
            moment = _as_moment(occurred_at)
            return start_instant <= moment <= end_instant

        session_loot = [event for event in loot if in_window(event.occurred_at)]
        session_deaths = [event for event in deaths if in_window(event.occurred_at)]
        session_trades = [event for event in trades if in_window(event.occurred_at)]

        account_name = next(
            (event.account_name for event in (*session_loot, *session_deaths, *session_trades)),
            "",
        )
        if not account_name:
            account_name = next(
                (
                    point.account_name
                    for point in reversed(skills_history)
                    if start_instant <= _as_moment(point.captured_at) <= end_instant
                ),
                "",
            )

        by_npc: dict[str, LootByNpc] = {}
        for event in session_loot:
            existing = by_npc.get(event.npc_name)
            by_npc[event.npc_name] = LootByNpc(
                npc_name=event.npc_name,
                kills=(existing.kills if existing else 0) + 1,
                value=(existing.value if existing else 0) + event.total_value,
            )
        loot_by_npc = tuple(sorted(by_npc.values(), key=lambda entry: entry.value, reverse=True))

        xp_gained, levels_gained = self._session_skill_deltas(skills_history, start_instant)
        net_worth_change = self._session_net_worth_change(net_worth_history, start_instant)

        return SessionSummary(
            account_hash=account_hash,
            account_name=account_name,
            started_at=start_instant.isoformat(),
            ended_at=end_instant.isoformat(),
            duration_minutes=max(0, round((end_instant - start_instant).total_seconds() / 60)),
            loot_value=sum(event.total_value for event in session_loot),
            loot_by_npc=loot_by_npc,
            death_count=len(session_deaths),
            value_lost=sum(event.total_value for event in session_deaths),
            trade_count=len(session_trades),
            trade_net_value=sum(event.estimated_difference for event in session_trades),
            xp_gained=xp_gained,
            levels_gained=levels_gained,
            net_worth_change=net_worth_change,
        )

    def _session_skill_deltas(
        self, skills_history: list[SkillsPoint], start_instant: datetime
    ) -> tuple[dict[str, int], dict[str, int]]:
        baseline, endpoint = self._bracket(skills_history, start_instant)
        return (
            self._positive_deltas(baseline.xp if baseline else {}, endpoint.xp if endpoint else {}),
            self._positive_deltas(
                baseline.skills if baseline else {}, endpoint.skills if endpoint else {}
            ),
        )

    def _session_net_worth_change(
        self, net_worth_history: list[NetWorthPoint], start_instant: datetime
    ) -> int | None:
        baseline, endpoint = self._bracket(net_worth_history, start_instant)
        if baseline is None or endpoint is None:
            return None
        return endpoint.total_value - baseline.total_value

    @staticmethod
    def _bracket[T](history: list[T], start_instant: datetime) -> tuple[T | None, T | None]:
        if not history:
            return None, None
        baseline = next(
            (
                point
                for point in reversed(history)
                if _as_moment(point.captured_at) <= start_instant
            ),
            history[0],
        )
        return baseline, history[-1]

    @staticmethod
    def _positive_deltas(baseline: dict[str, int], endpoint: dict[str, int]) -> dict[str, int]:
        result: dict[str, int] = {}
        for skill, value in endpoint.items():
            delta = value - baseline.get(skill, value)
            if delta > 0:
                result[skill] = delta
        return result

    def get_loadout_snapshot(self, account_hash: str) -> LoadoutSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM loadout_snapshots WHERE account_hash = ?", (account_hash,)
            ).fetchone()
        return self._row_to_loadout_snapshot(row) if row is not None else None

    def get_latest_loadout_snapshot(self) -> LoadoutSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM loadout_snapshots ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
        return self._row_to_loadout_snapshot(row) if row is not None else None

    @staticmethod
    def _row_to_loadout_snapshot(row: sqlite3.Row) -> LoadoutSnapshot:
        def load(raw: str) -> tuple[LoadoutItem, ...]:
            return tuple(
                LoadoutItem(
                    item_id=int(entry["item_id"]),
                    item_name=str(entry["item_name"]),
                    quantity=int(entry["quantity"]),
                    unit_value=int(entry["unit_value"]),
                )
                for entry in json.loads(raw)
            )

        return LoadoutSnapshot(
            account_hash=str(row["account_hash"]),
            account_name=str(row["account_name"]),
            captured_at=str(row["captured_at"]),
            equipment=load(str(row["equipment_json"])),
            inventory=load(str(row["inventory_json"])),
            bank=load(str(row["bank_json"])),
            skills={str(k): int(v) for k, v in json.loads(str(row["skills_json"])).items()},
            xp={str(k): int(v) for k, v in json.loads(str(row["xp_json"])).items()},
        )
