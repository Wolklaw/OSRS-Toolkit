from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from osrs_toolkit.ranking import ge_tax

# Bounded per account so a long-lived sync history can't grow this table without limit.
MAX_NET_WORTH_HISTORY_PER_ACCOUNT = 2_000

# Same cap and reasoning as net worth history, for skill levels over time.
MAX_SKILLS_HISTORY_PER_ACCOUNT = 2_000

# occurred_at is stored as text from the plugin, not a clean sortable instant (fields
# omitted when zero, or a non-UTC offset), so text comparison is only safe as a loose lower
# bound to cut away old rows cheaply. Callers still filter exactly on what comes back.
NOT_BEFORE_SAFETY_MARGIN = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: int
    recorded_at: str
    item_name: str
    quantity: int
    buy_price: int
    sell_price: int
    # Name this trade goes by outside its own database. Optional so a TradeRecord built by
    # hand in a test doesn't need to invent one.
    sync_uid: str | None = None

    @property
    def tax_each(self) -> int:
        return ge_tax(self.sell_price)

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
    # Which synced character this belongs to. None means unassigned (opened before this
    # column existed, or entered by hand) — treated as belonging to every character when
    # filtering, not to none of them.
    account_hash: str | None = None

    @property
    def estimated_profit(self) -> int:
        buy_price = self.actual_buy or self.buy_suggestion
        sell_price = self.sell_suggestion
        return (sell_price - buy_price - ge_tax(sell_price)) * self.unsold_stock

    @property
    def buy_suggestion(self) -> int:
        return self.current_buy_suggestion or self.target_buy

    @property
    def sell_suggestion(self) -> int:
        return self.current_sell_suggestion or self.target_sell

    @property
    def asking_price(self) -> int:
        """The price actually listed on the GE, not the app's suggestion.

        Falls back to the suggestion for a position never listed through a synced offer.
        Grading a live offer against the suggestion instead would flag a relisted position
        that already matches the market.
        """
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
        return sum(
            (fill.sell_price - self.actual_buy - ge_tax(fill.sell_price)) * fill.quantity
            for fill in self.sale_fills
        )

    @property
    def sold_quantity(self) -> int:
        return sum(fill.quantity for fill in self.sale_fills)

    @property
    def unsold_stock(self) -> int:
        """Units held and still to sell.

        Where buy fills are recorded, only what they actually bought counts as stock — a
        position that stopped buying part way shouldn't have profit projected on units
        never paid for. With no fills, the tracked quantity is the best estimate (same
        reading ``invested`` takes).
        """
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
        """Capital actually committed, from recorded buy fills where there are any.

        A part-bought position has only spent what those fills cost — pricing the full
        tracked quantity would overstate the "Capital traded" summary. With no fills yet,
        the planned outlay is the best estimate.
        """
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

    @property
    def owned_item_ids(self) -> frozenset[int]:
        """Items available for a PvM trip: equipped, carried, or one bank trip away."""
        return frozenset(item.item_id for item in (*self.equipment, *self.inventory, *self.bank))

    def quantity_owned(self, item_id: int) -> int:
        return sum(
            item.quantity
            for item in (*self.equipment, *self.inventory, *self.bank)
            if item.item_id == item_id
        )

    @property
    def total_value(self) -> int:
        """Priced at each item's own stamped ``unit_value`` (the plugin's GE snapshot at
        capture time), not a live lookup — stays correct even read back long afterward."""
        return sum(item.total_value for item in (*self.equipment, *self.inventory, *self.bank))


@dataclass(frozen=True, slots=True)
class NpcLootRecord:
    """One delivery of loot from an NPC kill, as observed — not a drop-table simulation."""

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
    """What was equipped and carried when a death animation started — not what was lost.
    Skull state, Protect Item, and wilderness rules aren't simulated here; diff against the
    next loadout snapshot to find the real loss."""

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
    """One historical net-worth reading, recorded the moment a loadout snapshot arrived."""

    account_hash: str
    account_name: str
    captured_at: str
    total_value: int


@dataclass(frozen=True, slots=True)
class SkillsPoint:
    """One historical skills reading, recorded the moment a loadout snapshot arrived."""

    account_hash: str
    account_name: str
    captured_at: str
    skills: dict[str, int]

    @property
    def total_level(self) -> int:
        return sum(self.skills.values())


@dataclass(frozen=True, slots=True)
class OfferOpened:
    """A GE offer just placed, with nothing filled yet — no coins or items moved, so unlike
    a fill this has nothing to record in the synced-trade activity log. It exists to start
    (or advance) Journal tracking the moment the player commits to an offer.

    ``restored`` marks an offer the game re-sent on login or a world hop rather than one the
    player just placed — the plugin can't always tell the two apart. Events from plugin
    builds predating the flag arrive as False, the safer reading.
    """

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
    """A GE offer the player cancelled. Whatever filled beforehand already arrived as fills;
    this just marks the offer dead so a position still waiting on it can resolve. Carries
    the same fields as OfferOpened since those identify the position it leaves behind."""

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
    """Whether ``incoming`` is at least as current as ``existing``.

    Ties count as newer, not just strictly-after: the touch trigger stamps ``updated_at`` to
    millisecond precision, and two edits to the same row inside one import pass -- two fills
    landing back to back -- routinely collide on that stamp. A strict ``>`` reads a tie as
    nothing changed and skips the row, fills included, which does not correct itself: every
    later export of that same server-side state carries the identical timestamp, so the row
    stays stuck at whichever version was first observed, forever.

    Parsed rather than compared as text, since "+00:00" vs "Z" would sort wrong as strings
    despite meaning the same instant.
    """
    return _as_moment(incoming) >= _as_moment(existing)


def _as_moment(stamp: object) -> datetime:
    if not isinstance(stamp, str) or not stamp:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _now() -> str:
    """Stamp used to decide which side of a sync wins, at microsecond precision.

    Not truncated to seconds like ``recorded_at``: two edits in the same second would
    otherwise share a stamp and neither could be seen as newer than the other, losing
    deletes and edits that land right on a client's watermark.
    """
    return datetime.now(UTC).isoformat()


class _Unchanged:
    """Sentinel for "leave this field alone", where ``None`` is itself a real value.

    ``account_hash`` is the case that needs it: None means "belongs to no character in
    particular", which is a thing a caller may well want to set on purpose.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNCHANGED"


UNCHANGED = _Unchanged()


@dataclass(frozen=True, slots=True)
class RetagResult:
    """What ``retag_positions`` did: how many it filed, and what it would not."""

    updated: int
    #: Positions left alone because the requested status has an invariant they fail.
    skipped: tuple[int, ...] = ()


class JournalRepository:
    _TERMINAL_STATUSES = frozenset({"Completed", "Cancelled"})
    #: Every status a tracked position can be filed under, so a bulk retag can refuse a
    #: typo rather than writing a status nothing else in the app knows how to read.
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

    # What a row needs before it can be recognised on another machine. ``trades`` and
    # ``tracked_trades`` are the only tables without a global identity of their own —
    # everything else already carries an ``event_id`` or is keyed by character.
    #
    # ``sync_uid`` is minted locally (SQLite has no uuid of its own), so a row created
    # offline still has its final identity. ``deleted_at`` is a tombstone rather than a real
    # delete: a missing row is otherwise ambiguous between "deleted there" and "not pushed
    # yet", and guessing wrong either resurrects deletes or silently eats new rows.
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
        """Copy the journal aside when this launch is about to change its shape.

        Only for a migrating launch — ``_create_startup_backup`` runs after ``_initialize``
        and would otherwise only ever keep already-migrated copies, and with just ten
        backups kept, the pre-migration state would roll off within weeks of daily use.
        """
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
            # A journal too damaged to read can't be backed up; don't block startup for it.
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

    # The tables that sync, and their local primary key. Everything else in this journal
    # already has a shared identity both sides agree on, so it merges on what it is rather
    # than needing to be reconciled.
    _SYNC_TABLES: ClassVar[dict[str, str]] = {
        "trades": "trade_id",
        "tracked_trades": "position_id",
    }

    # Fills have no identity of their own — carried with their position and replaced
    # wholesale when it changes rather than merged row by row, since they're derived from
    # the position's own history.
    _FILL_TABLES: ClassVar[dict[str, tuple[str, ...]]] = {
        "tracked_sale_fills": ("quantity", "sell_price"),
        "tracked_buy_fills": ("quantity", "buy_price"),
    }

    def sync_version(self) -> dict:
        """Cheap summary of table state for polling — two indexed scalars per table, nothing
        serialized. The pull that actually costs something only happens once this moves."""
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
        """Every row that changed after ``since``, tombstones included — unlike every
        ordinary read, which filters them out."""
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
                    # .keys() is load-bearing: iterating a sqlite3.Row yields values, not
                    # column names, so SIM118's fix would build a record keyed by the data.
                    record = {
                        key: row[key]
                        for key in row.keys()  # noqa: SIM118
                        if key != primary_key
                    }
                    if not record.get("sync_uid"):
                        # Nothing to recognise it by on the other side; sending it would only
                        # create a duplicate. Cannot happen after the migration.
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
        """Merge rows in, newest ``updated_at`` winning. Returns what changed.

        Last-write-wins: a row older than the one already here is dropped, so a slow client
        replaying stale state can't walk the journal backwards. Unknown columns are ignored
        rather than rejected, so a newer build on one side can send a column an older build
        on the other has never heard of without failing the whole sync.
        """
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
        """Stamp ``updated_at`` automatically on any edit that doesn't set it explicitly.

        Otherwise every write path — recording a fill, reviewing a suggestion, listing a
        price — has to remember to move the stamp itself. The guard lets ``sync_apply``
        write the other side's ``updated_at`` without this overwriting it (and stops the
        trigger from re-entering its own UPDATE).

        Installed last, after every migration above, since those rewrite rows without a
        person having touched anything.
        """
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

    def _migrate_sync_columns(
        self, connection: sqlite3.Connection, table: str, primary_key: str, stamp_from: str
    ) -> None:
        """Add sync_uid/updated_at/deleted_at to a table and backfill existing rows.

        ``updated_at`` backfills from the row's own existing timestamp, not "now" — "now"
        would make every existing row look freshly edited and win every conflict on first
        contact with the other side.
        """
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
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            # Migrations below rewrite rows wholesale (backfilling a suggestion, stamping a
            # completion, giving an old row its sync_uid). None of that is a person editing a
            # trade, so triggers are dropped for the duration rather than trusting every
            # statement to preserve the stamp.
            for table in self._SYNC_TABLES:
                connection.execute(f"DROP TRIGGER IF EXISTS {table}_touch_updated_at")
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
            # An opening/cancellation moves no coins, so it has no synced_trades row — but it
            # still edits a position, and applying it twice would duplicate or delete one.
            # The queue file is deleted after applying, and deletes can fail on Windows, so
            # track which ones already landed.
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS skills_history_captured ON skills_history(captured_at)"
            )
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
            # Backfill completion time for positions saved before it existed, so period
            # filters don't silently drop old history.
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
            # Preserve completed positions created before variable-price fills existed.
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
            # Preserve positions recorded before variable-price buy fills existed: their
            # single actual_buy price becomes one fill covering the whole quantity.
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
            self._install_touch_triggers(connection)

    def add(self, item_name: str, quantity: int, buy_price: int, sell_price: int) -> int:
        with self._connect() as connection:
            # Named at birth, not when synced — a trade recorded offline needs its final
            # identity from the start.
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
        """What a trade is called outside this database, given its local id.

        For a caller that just created one and needs to reference it elsewhere — the website
        attributes trades to characters by sync_uid, not by this autoincrement id, which
        means a different trade on every machine.
        """
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
        """Mark a trade deleted rather than removing the row.

        A row that's simply gone is indistinguishable, to the other side of a sync, from one
        never synced — so a real delete here would be undone by the next pull. The tombstone
        is what lets deletion travel; every read filters it out.
        """
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

    def list_tracked(self, account_hash: str | None = None) -> list[TrackedTrade]:
        """Every tracked position, or just one character's plus anything never tagged to one.

        An untagged position has no owner to exclude it, so it's kept in every filtered read
        rather than only the unfiltered one.
        """
        with self._connect() as connection:
            if account_hash is None:
                rows = connection.execute(
                    "SELECT * FROM tracked_trades WHERE deleted_at IS NULL"
                    " ORDER BY created_at DESC, position_id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM tracked_trades
                    WHERE deleted_at IS NULL AND (account_hash = ? OR account_hash IS NULL)
                    ORDER BY created_at DESC, position_id DESC
                    """,
                    (account_hash,),
                ).fetchall()
            fill_rows = connection.execute(
                "SELECT * FROM tracked_sale_fills ORDER BY fill_id ASC"
            ).fetchall()
            buy_fill_rows = connection.execute(
                "SELECT * FROM tracked_buy_fills ORDER BY fill_id ASC"
            ).fetchall()
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
        """When this position finished, for the status it's being saved with.

        Stamped once, the first time it reaches a terminal status. Re-saving an already
        terminal position (e.g. correcting a fill price later) keeps the original time
        rather than dragging it into today's period filters.
        """
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
    ) -> int | None:
        """Match a synced GE fill to the oldest eligible tracked position and record it as a
        buy or sale fill, transitioning status when it completes a side.

        Only applied when the whole fill fits in the position's remaining room — a fill that
        overshoots what was tracked is left for manual reconciliation rather than split or
        rejected. A buy fill with no eligible position creates one, sized to the offer's full
        ``total_quantity`` when known (so the offer's remaining fills keep landing on it
        instead of each spawning a duplicate), else sized to just this fill. A created
        position's sell suggestion is seeded from ``suggested_sell_price`` when it beats what
        was paid, but ``target_sell`` still mirrors the buy price so it's never mistaken for
        a plan the player made on purpose (see ``apply_offer_opened``). A position manually
        marked 'Supplies' keeps that status through further fills rather than being pulled
        back into the ordinary lifecycle.

        A sale only matches a still-'Pending buy' position once no ordinary holding can
        absorb it. Such a position bought less than it set out to (offer cancelled or
        undersized) and can never reach 'Bought' on its own, so its stock would otherwise be
        unsellable — matching it resizes the position down to what it actually bought and
        lets it continue through the ordinary lifecycle. This ends that position early and
        the offer's remaining fills open a row of their own, but the alternative leaves every
        part-bought position permanently stuck, which is the case that actually happens.

        Returns the matched or newly created position_id, or None for an unmatched sell.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        eligible_statuses = (
            ("Pending buy", "Supplies")
            if side == "buy"
            else ("Bought", "Listed for sale", "Partially sold", "Supplies")
        )
        # Restricted to this character's own positions (plus any never tagged to one), so a
        # fill on one character can't absorb into another character's position for the same
        # item.
        tracked = [trade for trade in self.list_tracked(account_hash) if trade.item_id == item_id]
        candidates = sorted(
            (trade for trade in tracked if trade.status in eligible_statuses),
            key=lambda trade: trade.created_at,
        )
        if side == "sell":
            # Last resort, behind every ordinary holding: a part-bought position's stock is
            # still stock, and a sale has to land somewhere.
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
                # sale_fills stays untouched; actual_sell passed through so update_tracked
                # doesn't blank the already-recorded sell average.
                self.update_tracked(
                    trade.position_id, new_status, None, trade.actual_sell, None, new_buy_fills
                )
            else:
                # A still-buying position holds only what it's bought; a finished one holds
                # everything it was tracked for, fills or not (a manual entry has none).
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
                # buy_fills stays untouched; actual_buy passed through so update_tracked
                # doesn't blank the already-recorded buy average.
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
            # No reliable size to create a position at without the offer's real total —
            # sizing from a single partial fill would mark it complete immediately, and the
            # offer's next fill would spawn a duplicate "complete" position. Leave it for the
            # activity feed until a fill reports the real total, or an explicit offer-opened
            # event starts tracking it properly.
            return None
        position_id = self.track(
            item_id,
            item_name,
            total_quantity,
            unit_price,
            unit_price,
            account_hash=account_hash,
        )
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
    ) -> int | None:
        """Start (or advance) tracking the moment an offer is placed, before anything fills.

        A buy offer first looks for a matching untouched plan (still "Pending buy", nothing
        bought, real sell target above buy target) and adopts it instead of opening a second
        row — tracking a suggested flip and then placing that exact offer used to produce a
        duplicate row priced buy == sell, which always read as a guaranteed loss. A created
        position's sell suggestion is seeded from ``suggested_sell_price`` when it beats the
        offer price, same as ``apply_synced_ge_fill``; ``target_sell`` still mirrors
        ``target_buy`` so a position this method creates is always told apart from one the
        player planned.

        ``restored`` says the game re-sent this offer on login or a world hop rather than the
        player placing it. A re-sent offer is already running, so it belongs to whatever
        in-flight position is tracking it — that claim outranks an untouched plan, since a
        re-sent offer can't be the placing of one. Only when nothing is tracking it does it
        fall back to a plan or start a new position, which is how an offer placed elsewhere
        (mobile, or while this app was closed) still reaches the Journal. Without the flag
        (older plugin builds), a re-announced offer is only recognised when it matches an
        in-flight position with fills exactly, in both size and price — a stricter match
        that costs an occasional merged row rather than a duplicate on every world hop.

        A sell offer only nudges an existing "Bought" position to "Listed for sale"; there's
        nothing to create from a sell alone. Failing that, it falls back in order to: a
        restored offer matching an already-"Listed for sale" position (counts as matched); a
        "Pending buy" position with fills (stopped buying part way, resized to what it
        actually bought and listed); or a "Partially sold" position being relisted (status
        untouched, price recorded as the new ask). Either way the price is written via
        ``record_listed_price``.

        Returns the position_id touched, or None for a sell with nothing eligible.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        # Restricted the same way apply_synced_ge_fill is: to this character's own positions
        # plus any never tagged to one.
        by_age = sorted(self.list_tracked(account_hash), key=lambda trade: trade.created_at)
        if side == "buy":
            untouched_plan = next(
                (
                    trade
                    for trade in by_age
                    if trade.item_id == item_id
                    and trade.status == "Pending buy"
                    and trade.bought_quantity == 0
                    and trade.target_sell > trade.target_buy
                ),
                None,
            )
            already_tracked = self._offer_already_tracked(
                by_age, item_id, total_quantity, offer_price, restored
            )
            # A newly placed offer is a plan being acted on, so the plan takes it. A re-sent
            # offer is already running and tracked elsewhere — taking the plan there would
            # adopt a flip never placed and leave the real position behind.
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
                if suggested_sell_price is not None and suggested_sell_price > offer_price:
                    self.review_suggestion(position_id, offer_price, suggested_sell_price)
                return position_id
            if total_quantity > planned.quantity:
                # Buying more than planned: grow the position so its fills have room to land.
                # The planned prices are deliberately left as the player set them.
                self.update_tracked(
                    planned.position_id,
                    planned.status,
                    planned.actual_buy,
                    planned.actual_sell,
                    quantity=total_quantity,
                )
            return planned.position_id
        candidate = next(
            (trade for trade in by_age if trade.item_id == item_id and trade.status == "Bought"),
            None,
        )
        if candidate is None and restored:
            already_listed = next(
                (
                    trade
                    for trade in by_age
                    if trade.item_id == item_id and trade.status == "Listed for sale"
                ),
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
                    if trade.item_id == item_id
                    and trade.status == "Pending buy"
                    and trade.bought_quantity > 0
                ),
                None,
            )
            if candidate is None:
                # A part-sold position keeps its status when its sell offer is cancelled, so
                # relisting the rest changes nothing but the price — without recording it the
                # row stays flagged against the ask the player just abandoned.
                part_sold = next(
                    (
                        trade
                        for trade in by_age
                        if trade.item_id == item_id and trade.status == "Partially sold"
                    ),
                    None,
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
        # The moment the app learns what the player is really asking — without it, "Needs
        # attention" keeps grading listings against the app's own original target.
        self.record_listed_price(candidate.position_id, offer_price)
        return candidate.position_id

    @staticmethod
    def _offer_already_tracked(
        by_age: list[TrackedTrade],
        item_id: int,
        total_quantity: int,
        offer_price: int,
        restored: bool,
    ) -> TrackedTrade | None:
        """The position a buy offer is already tracked by, when the offer is not a new one.

        Only consulted once no untouched plan matched, so every candidate here either has
        fills or was opened by an offer of its own. See ``apply_offer_opened`` for why a
        restored offer may adopt any of them while an unflagged one needs an exact match.
        """
        in_flight = [
            trade
            for trade in by_age
            if trade.item_id == item_id
            and trade.status in ("Pending buy", "Supplies")
            and trade.bought_quantity < trade.quantity
        ]
        if not in_flight:
            return None
        if restored:
            # Oldest wins, except that a position sized to this very offer is the better claim.
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
        """Resolve the position a cancelled GE offer leaves behind.

        A cancelled buy that never filled is deleted — it only existed because the offer was
        placed. One that part-filled is resized down to what actually bought and marked
        "Bought", the same reconciliation the Journal otherwise asks the player to do by
        hand. A cancelled sell with nothing sold drops back to "Bought" so it can be
        relisted; one that already part-sold is left alone, since those sales really
        happened.

        A cancelled buy is matched strictly (same size and price) since getting it wrong
        deletes or resizes a position the player may have built by hand. A cancelled sell
        only flips a status back, which costs nothing to undo, so it matches the oldest
        listing for the item. A part-filled buy already at "Supplies" is resized down the
        same way but keeps that status rather than being pulled into the ordinary lifecycle.

        Only a position an offer opened for itself is deleted, told apart by the same
        target_sell/target_buy invariant ``apply_offer_opened`` maintains — otherwise
        cancelling an offer that had adopted a player's plan would delete that plan.

        Returns the position_id touched, or None when nothing matched.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        by_age = sorted(self.list_tracked(account_hash), key=lambda trade: trade.created_at)
        if side == "sell":
            # A sell offer never created the position, so its price/size don't describe how
            # the position was tracked. Status is the honest thing to match on.
            candidate = next(
                (
                    trade
                    for trade in by_age
                    if trade.item_id == item_id and trade.status == "Listed for sale"
                ),
                None,
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
                if trade.item_id == item_id
                and trade.status in ("Pending buy", "Supplies")
                and trade.quantity == total_quantity
                and trade.target_buy == offer_price
            ),
            None,
        )
        if candidate is None:
            return None
        if candidate.bought_quantity == 0:
            if candidate.target_sell > candidate.target_buy:
                # A player-made plan, adopted rather than created by the offer. Still tracked
                # and not yet placed — exactly the state it's already in.
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
        """Change how a batch of positions is filed, without touching what they hold.

        For the case a one-at-a-time editor makes miserable: a long shopping trip lands
        twenty positions and every one of them is filed wrong. Each argument left out is left
        alone, so this sets only what was actually asked for.

        Deliberately not routed through ``update_tracked``. That one recomputes
        ``actual_buy``/``actual_sell`` from the fills it is handed and writes the result
        unconditionally, so calling it with no fills — which is exactly what a "just change
        the status" caller has — blanks both averages. Nothing here reads or writes a fill,
        an average or a quantity at all.

        ``Completed`` is the one status with an invariant behind it (a finished position has
        to account for its full quantity on both sides), so positions that cannot satisfy it
        are skipped and counted rather than written into a state the single-row editor would
        have refused.
        """
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
        """Whether this position already accounts for its full quantity on both sides.

        The same bar ``update_tracked`` holds a single position to when it is saved as
        Completed, asked here without rewriting anything.
        """
        return trade.bought_quantity == trade.quantity and trade.sold_quantity == trade.quantity

    def delete_tracked(self, position_id: int) -> None:
        """Tombstoned, not removed — see ``delete``. Its sale fills are left alone: they are
        reachable only through the position, which no read returns any more."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE tracked_trades SET deleted_at = ?, updated_at = ? WHERE position_id = ?",
                (_now(), _now(), position_id),
            )

    def record_listed_price(self, position_id: int, sell_price: int) -> None:
        """Record the price a sell offer was actually placed at.

        Kept apart from ``review_suggestion`` (the app revising its own advice, which stamps
        ``suggestion_reviewed_at``): this writes only the ask, leaving the suggestion and
        plan untouched. Never cleared when the offer ends — cancelling a listing doesn't
        un-choose the price the player picked. The next listing overwrites it.
        """
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
        """Claim a GE offer opening or cancellation, returning False if already applied.

        Claimed before the event is applied, so a crash in between costs one event instead of
        replaying it.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO applied_offer_events (event_id, applied_at) VALUES (?, ?)",
                (event_id, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            return cursor.rowcount > 0

    def add_synced_trades(self, trades: list[SyncedTrade]) -> list[bool]:
        """Insert a batch of trades over one connection instead of one per trade."""
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
        """Imported RuneLite activity, newest first.

        ``since`` bounds the read for callers that only look at a recent window (see
        ``NOT_BEFORE_SAFETY_MARGIN``); without it this loads every event ever imported.
        ``account_hash`` is exact here, unlike ``list_tracked`` — every synced trade carries
        a real character, so there's no untagged case to fall back into.
        """
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
        """Look up one synced trade without reloading every event's items."""
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
        """Insert one loot delivery, returning False if its event_id already landed."""
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
        """Imported loot deliveries, newest first — same filter/window shape as
        ``list_synced_trades``."""
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
        """Insert one death snapshot, returning False if its event_id already landed."""
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
        """Imported death snapshots, newest first — same filter/window shape as
        ``list_synced_trades``."""
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
        """Replace the previous snapshot for this account and append its total value to
        the net-worth and skills history."""

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

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO loadout_snapshots (
                    account_hash, account_name, captured_at, equipment_json, inventory_json,
                    bank_json, skills_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_hash) DO UPDATE SET
                    account_name = excluded.account_name,
                    captured_at = excluded.captured_at,
                    equipment_json = excluded.equipment_json,
                    inventory_json = excluded.inventory_json,
                    bank_json = excluded.bank_json,
                    skills_json = excluded.skills_json
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    dump(snapshot.equipment),
                    dump(snapshot.inventory),
                    dump(snapshot.bank),
                    json.dumps(snapshot.skills, separators=(",", ":")),
                ),
            )
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
                    account_hash, account_name, captured_at, skills_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.account_hash,
                    snapshot.account_name,
                    snapshot.captured_at,
                    json.dumps(snapshot.skills, separators=(",", ":")),
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
            )
            for row in rows
        ]

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
        )
