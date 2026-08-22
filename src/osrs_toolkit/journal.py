from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from osrs_toolkit.ranking import ge_tax

# One row per synced loadout snapshot, per account — bounded so an account synced for years
# cannot grow this table without limit. Plenty for a readable net-worth chart either way.
MAX_NET_WORTH_HISTORY_PER_ACCOUNT = 2_000

# ``occurred_at`` is stored as the plugin wrote it, and comparing those as text is only
# roughly comparing them as instants: the writer omits fields that are zero (so "05:00Z"
# sorts after "05:00:30Z"), and a value could carry a zone offset rather than UTC. A SQL
# bound is therefore only ever used to cut away the distant past cheaply, wide enough that
# neither can reach across it; callers still filter exactly, as instants, on what comes back.
NOT_BEFORE_SAFETY_MARGIN = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: int
    recorded_at: str
    item_name: str
    quantity: int
    buy_price: int
    sell_price: int

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
    # Which synced character this position belongs to, or None for one opened before this
    # column existed, or entered by hand with no character context to attach. A caller
    # filtering by character treats None as belonging to every character, rather than to
    # none of them — an untagged position is unassigned, not somebody else's.
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
        """What this position is actually asking, which is not the same as what it suggests.

        ``sell_suggestion`` is the app's target — where the flip was planned to sell. The ask
        is the price the player really typed into the Grand Exchange, recorded by
        ``record_listed_price`` when the sell offer opened. Only the ask can say whether a
        listing has gone stale: grading a live offer against a target the player already
        chose to ignore left a relisted position flagged for attention at the very price the
        market was suggesting. Falls back to the suggestion for a position never listed
        through a synced offer, which is the best statement of intent available for it.
        """
        return self.listed_sell_price or self.sell_suggestion

    @property
    def suggestion_was_refreshed(self) -> bool:
        return bool(
            self.suggestion_reviewed_at
            and self.suggestion_reviewed_at[:10] > self.created_at[:10]
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
        """Units held and still to sell, which is what a profit estimate can be made on.

        Where buy fills are recorded, only what they bought is stock: a position whose buying
        stopped part way holds less than it was tracked for, and pricing the rest projects
        profit on units never bought and never paid for. With no fills recorded — a plan not
        yet placed, or a position entered by hand — the tracked quantity is the best statement
        of what it holds, the same reading ``invested`` takes of the same two cases.
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
        """Capital actually committed, from the recorded buy fills where there are any.

        A position that is only part-bought has only spent what those fills cost, so
        pricing the full tracked quantity would overstate the "Capital traded" summary.
        With no fills yet, nothing has been spent and the planned outlay is the best
        estimate available.
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
        return frozenset(
            item.item_id for item in (*self.equipment, *self.inventory, *self.bank)
        )

    def quantity_owned(self, item_id: int) -> int:
        return sum(
            item.quantity
            for item in (*self.equipment, *self.inventory, *self.bank)
            if item.item_id == item_id
        )

    @property
    def total_value(self) -> int:
        """Everything equipped, carried, or banked, priced at each item's own stamped
        ``unit_value`` — the plugin's own GE-price snapshot at capture time, not a live
        lookup, so this stays correct even for a snapshot read back long afterward."""
        return sum(item.total_value for item in (*self.equipment, *self.inventory, *self.bank))


@dataclass(frozen=True, slots=True)
class NetWorthPoint:
    """One historical net-worth reading, recorded the moment a loadout snapshot arrived."""

    account_hash: str
    account_name: str
    captured_at: str
    total_value: int


@dataclass(frozen=True, slots=True)
class OfferOpened:
    """A GE offer that was just placed, with nothing filled yet — no coins or items have
    moved, so unlike a fill this has nothing to record in the synced-trade activity log.
    It exists purely to start (or advance) Journal tracking the moment the player commits
    to an offer instead of waiting for the first partial fill.

    ``restored`` marks an offer the game re-sent on login or a world hop rather than one the
    player just placed. The plugin cannot always tell a re-sent offer from a new one — its
    memory of the slot is lost with the client — so it says which case this is and the Journal
    decides: a restored offer belongs to a position that already exists, a new one may not.
    Events from plugin builds predating the flag arrive as False, the safer reading.
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
    """A GE offer the player cancelled. Nothing changes hands at the cancellation itself —
    whatever filled beforehand already arrived as fills — but the offer is dead, so a position
    still waiting on it can never resolve on its own. Carries the same fields as OfferOpened
    because those are what identify the position the offer left behind."""

    event_id: str
    account_hash: str
    account_name: str
    occurred_at: str
    side: str
    item_id: int
    item_name: str
    offer_price: int
    total_quantity: int


class JournalRepository:
    _TERMINAL_STATUSES = frozenset({"Completed", "Cancelled"})

    def __init__(self, database_path: Path | None = None) -> None:
        using_default_path = database_path is None
        if database_path is None:
            local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
            database_path = local_data / "OSRSToolkit" / "data" / "toolkit.db"
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if using_default_path:
            self._recover_or_migrate_default_database()
        self._initialize()
        if using_default_path:
            self._create_startup_backup()

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
            # An opening or cancellation moves no coins, so it has no place in synced_trades —
            # but it still edits a position, and doing that twice invents a row or deletes a
            # real one. The queue file it came from is deleted after it applies, and a delete
            # can fail (a locked file on Windows), so remember which ones already landed.
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
            # Positions saved before completion timestamps existed get a best-effort
            # backfill so period filters do not silently drop old history.
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

    def add(self, item_name: str, quantity: int, buy_price: int, sell_price: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trades (recorded_at, item_name, quantity, buy_price, sell_price)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    item_name.strip(),
                    quantity,
                    buy_price,
                    sell_price,
                ),
            )
            return int(cursor.lastrowid)

    def list_all(self) -> list[TradeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trades ORDER BY recorded_at DESC, trade_id DESC"
            ).fetchall()
        return [
            TradeRecord(
                trade_id=int(row["trade_id"]),
                recorded_at=str(row["recorded_at"]),
                item_name=str(row["item_name"]),
                quantity=int(row["quantity"]),
                buy_price=int(row["buy_price"]),
                sell_price=int(row["sell_price"]),
            )
            for row in rows
        ]

    def delete(self, trade_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))

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
                    suggestion_reviewed_at, account_hash
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending buy', ?, ?, ?, ?, ?)
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
                ),
            )
            return int(cursor.lastrowid)

    def list_tracked(self, account_hash: str | None = None) -> list[TrackedTrade]:
        """Every tracked position, or just one character's plus anything never tagged to one.

        An untagged position — opened before this column existed, or entered by hand with no
        character context — has no owner to exclude it from a character's view, so it is kept
        in every filtered read rather than only in the unfiltered one.
        """
        with self._connect() as connection:
            if account_hash is None:
                rows = connection.execute(
                    "SELECT * FROM tracked_trades ORDER BY created_at DESC, position_id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM tracked_trades
                    WHERE account_hash = ? OR account_hash IS NULL
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
                    int(row["listed_sell_price"])
                    if row["listed_sell_price"] is not None
                    else None
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
                    raise ValueError("A completed trade must account for the full quantity of buy fills")
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
                    [
                        (position_id, fill_quantity, price)
                        for fill_quantity, price in sale_fills
                    ],
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
                    [
                        (position_id, fill_quantity, price)
                        for fill_quantity, price in buy_fills
                    ],
                )

    def _completion_time(
        self, status: str, previous_status: str, previous_completed_at: str | None
    ) -> str | None:
        """When this position finished, for the status it is being saved with.

        Stamped the moment it first reaches a terminal status, and cleared again if it is
        reopened. A position that was terminal already keeps the time it actually finished:
        saving one again is an edit to recorded history — correcting a fill price weeks
        later, say — not a second completion. Re-stamping it would drag an old trade into
        today's period filters on the Journal and Performance pages, and stretch its
        recorded hold time to however long ago it really finished.
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
        """Match a synced GE fill to the oldest eligible tracked position for this item and
        record it as a buy or sale fill, transitioning status when it completes a side.

        Only applied to an existing position when the whole fill fits in its remaining room,
        so a fill that overshoots what was tracked is left for manual reconciliation instead
        of being silently split or rejected outright. A buy fill with no eligible tracked
        position creates one instead, sized to the GE offer's full total_quantity when known
        so the offer's remaining fills keep accumulating onto it rather than each spawning
        its own position; falls back to just this fill's quantity otherwise. A created
        position's current sell suggestion is seeded from ``suggested_sell_price`` when it
        beats what was actually paid, so an untracked flip still shows a real profit estimate
        instead of one that reads as guaranteed break-even; its target_sell still mirrors the
        buy price as before, so it is never mistaken for a plan the player made on purpose (see
        ``apply_offer_opened``). A position the player has manually reclassified to 'Supplies'
        (see ``JournalRepository.update_tracked``) keeps that status through further fills
        rather than being carried along the ordinary lifecycle — reclassifying it mid-fill must
        not stop it absorbing the rest of the same offer, or the offer's remaining fills would
        find nothing eligible and spawn a duplicate row.

        A sale is matched against a position that is still 'Pending buy' only once no ordinary
        holding can absorb it. Such a position bought less than it set out to — the offer behind
        it was cancelled part way, or was smaller than the plan it was placed against — so it
        can never reach 'Bought' on its own and its stock would otherwise be unsellable. Selling
        that stock is the player saying the buying is over, so the position is resized to what
        it actually bought and carries on through the ordinary lifecycle from there. Only the
        quantity bought is sellable, whatever the position was originally sized to. The cost is
        that listing part of a holding while its buy offer is still running ends that position
        early, and the offer's remaining fills open a row of their own; the alternative leaves
        every part-bought position permanently stuck, which is the case that actually happens.
        Returns the matched or newly created position_id, or None for an unmatched sell.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        eligible_statuses = (
            ("Pending buy", "Supplies")
            if side == "buy"
            else ("Bought", "Listed for sale", "Partially sold", "Supplies")
        )
        # Restricted to this character's own positions (plus any never tagged to one) once an
        # account_hash is given, so a fill on one character cannot absorb into a position another
        # character opened for the same item — two people flipping the same item through one
        # pairing token used to merge into whichever position happened to be oldest.
        tracked = [trade for trade in self.list_tracked(account_hash) if trade.item_id == item_id]
        candidates = sorted(
            (trade for trade in tracked if trade.status in eligible_statuses),
            key=lambda trade: trade.created_at,
        )
        if side == "sell":
            # Last resort, behind every ordinary holding: the stock a part-bought position
            # never got to finish buying is still stock, and a sale has to land somewhere.
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
                new_buy_fills = [
                    (fill.quantity, fill.buy_price) for fill in trade.buy_fills
                ]
                new_buy_fills.append((quantity, unit_price))
                new_status = (
                    trade.status
                    if trade.status == "Supplies"
                    else "Bought"
                    if trade.bought_quantity + quantity == trade.quantity
                    else "Pending buy"
                )
                # sale_fills stays untouched (None); actual_sell is passed through explicitly
                # so update_tracked doesn't blank out an already-recorded sell average.
                self.update_tracked(
                    trade.position_id, new_status, None, trade.actual_sell, None, new_buy_fills
                )
            else:
                # A position still buying holds only what it bought; one that finished holds
                # everything it was tracked for, fills recorded against it or not (a manually
                # entered position has none).
                part_bought = trade.status == "Pending buy"
                stock = trade.bought_quantity if part_bought else trade.quantity
                remaining = stock - trade.sold_quantity
                if remaining < quantity:
                    continue
                new_sale_fills = [
                    (fill.quantity, fill.sell_price) for fill in trade.sale_fills
                ]
                new_sale_fills.append((quantity, unit_price))
                new_status = (
                    trade.status
                    if trade.status == "Supplies"
                    else "Completed"
                    if trade.sold_quantity + quantity == stock
                    else "Partially sold"
                )
                # buy_fills stays untouched (None); actual_buy is passed through explicitly
                # so update_tracked doesn't blank out the already-recorded buy average.
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
            # Without the offer's real total_quantity there is no reliable size to create a
            # position at: guessing from a single partial fill (e.g. sizing it to just this
            # fill) would mark it "complete" immediately, so the offer's next fill finds
            # nothing eligible to match and creates yet another "complete" position — one
            # GE order fragmenting into several Journal rows. Better to leave it for the
            # RuneLite activity feed until a fill reports the real total, or an explicit
            # ge_offer_opened event starts tracking it properly from the outset.
            return None
        position_id = self.track(
            item_id, item_name, total_quantity, unit_price, unit_price,
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

        A buy offer first looks for a position the player already planned for this item — still
        "Pending buy", nothing bought, and carrying a real sell target above its buy target —
        and adopts that instead of opening a second row beside it. Tracking a suggested flip and
        then placing exactly that offer is the ordinary way to use the Journal, and it used to
        produce two rows: the plan, and a duplicate priced from the offer whose sell target
        equalled its buy target, so it always read as a guaranteed loss. A created position's
        current sell suggestion is seeded from ``suggested_sell_price`` when it beats the offer
        price, for the same reason ``apply_synced_ge_fill`` does — target_sell itself is left
        mirroring target_buy, so a position this method created is always told apart from one
        the player planned.

        ``restored`` says the game re-sent this offer on login or a world hop rather than the
        player placing it. A re-sent offer is one already running, so it belongs to whatever
        in-flight position is tracking it, however much has bought against it: adopting is
        right and opening a row beside it is the duplicate. That claim outranks an untouched
        plan, which a re-sent offer cannot be the placing of; only when nothing is tracking it
        does it fall back to a plan, or start a position of its own — which is how an offer
        placed elsewhere, on mobile or while this app was closed, still reaches the Journal.

        Without that flag, from an older plugin build, an offer arriving while an in-flight
        position for the item already has fills is read as that same offer re-announced when it
        matches the position exactly, in both size and price. Two separate offers agreeing on
        both is rare and costs a merged row; a re-announcement not recognised costs a duplicate
        row on every world hop, which is the case that actually happens. Anything else — a
        different price, a different size, or nothing filled yet — still opens its own row, so
        two genuinely independent offers stay on two rows.

        A sell offer only nudges an existing "Bought" position to "Listed for sale"; there is
        nothing to create from a sell alone, since a sale must already have something bought
        behind it. One already listed is what a restored sell offer is describing, so it counts
        as matched rather than as nothing at all. Failing both, a position still "Pending buy"
        with fills against it is a holding that stopped buying part way, and listing it says so:
        it is resized to what it actually bought and listed like any other holding, for the
        reasons ``apply_synced_ge_fill`` gives. Failing all of those, a part-sold position for
        the item is being relisted: nothing about its status changes, but the offer's price is
        recorded as its new ask. Either way the sell offer's price is written to the position it
        matched — see ``record_listed_price``. Returns the position_id touched, or None for a
        sell with nothing eligible.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        # Restricted the same way apply_synced_ge_fill is: to this character's own positions
        # plus any never tagged to one, so an offer placed on one character cannot adopt a plan
        # or an in-flight position that belongs to another.
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
            # Which claim on the offer is stronger depends on where it came from. A newly
            # placed offer is a plan being acted on, so the plan takes it. A re-sent one is
            # already running and something is already tracking it — taking a plan there
            # adopts a flip that was never placed, and resizes it to an offer that has
            # nothing to do with it, while the position doing the buying is left behind.
            planned = (
                already_tracked or untouched_plan
                if restored
                else untouched_plan or already_tracked
            )
            if planned is None:
                position_id = self.track(
                    item_id, item_name, total_quantity, offer_price, offer_price,
                    account_hash=account_hash,
                )
                if suggested_sell_price is not None and suggested_sell_price > offer_price:
                    self.review_suggestion(position_id, offer_price, suggested_sell_price)
                return position_id
            if total_quantity > planned.quantity:
                # Buying more than planned: grow the position to the offer actually placed so
                # its fills have room to land. Nothing already bought is invalidated by growing
                # it, and the planned prices are deliberately left as the player set them.
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
                # A part-sold position keeps its status when its sell offer is cancelled —
                # those sales really happened — so relisting the rest transitions nothing and
                # was dropped here entirely. The new price is the whole point of a relist, and
                # without it the row stays flagged against the ask the player just abandoned.
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
            quantity=(
                candidate.bought_quantity if candidate.status == "Pending buy" else None
            ),
        )
        # The one moment the app learns what the player is really asking. Dropping it left the
        # Needs attention flag grading every listing against the app's own original target, so
        # relisting at the market's suggestion cleared nothing and the row stayed flagged.
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

        Only consulted once no untouched plan matched, so every candidate here either has fills
        against it or was opened by an offer of its own. See ``apply_offer_opened`` for why a
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

        A cancelled buy that never filled leaves a position that can never complete; it only
        existed because the offer was placed, so it goes away with the offer. One that part
        filled is resized down to what actually bought and marked "Bought" — the same
        reconciliation the Journal otherwise asks the player to do by hand. A cancelled sell
        with nothing sold drops back to "Bought" so it can be relisted; one that already part
        sold is left alone, because those sales really happened.

        A cancelled buy is matched strictly — same size and price as the offer — because getting
        it wrong deletes or resizes a position the player may have built by hand. A cancelled
        sell only flips a status back, which costs nothing to undo, so it matches the oldest
        listing for the item exactly as apply_offer_opened picked one to list in the first place.
        A part-filled buy already sitting at "Supplies" is resized down the same way but keeps
        that status rather than being pulled back into the ordinary Bought lifecycle. Returns
        the position_id touched, or None when nothing matched.

        Only a position an offer opened for itself is deleted, told apart by the invariant
        ``apply_offer_opened``/``apply_synced_ge_fill`` maintain: a position they create has
        target_sell mirroring target_buy, where one the player planned carries a real sell
        target above its buy target. Tracking a suggested flip and then placing exactly that
        offer means ``apply_offer_opened`` adopted the plan rather than opening a row beside
        it, so without that check cancelling the offer deleted the plan the player made.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        by_age = sorted(self.list_tracked(account_hash), key=lambda trade: trade.created_at)
        if side == "sell":
            # A sell offer never created the position — a buy did — so neither its price nor its
            # size describes how the position was tracked: the player can list part of one, or
            # cover one listing with several offers. Status is the honest thing to match on.
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
                # A plan the player made, adopted by the offer rather than created by it. The
                # offer is gone but the plan is not: it is back to being tracked and not yet
                # placed, which is exactly the state it is already in.
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

    def delete_tracked(self, position_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM tracked_trades WHERE position_id = ?", (position_id,)
            )

    def record_listed_price(self, position_id: int, sell_price: int) -> None:
        """Record the price a sell offer was actually placed at.

        Kept apart from ``review_suggestion``: that is the app revising its own advice, and it
        stamps ``suggestion_reviewed_at``, which the Journal shows as the ↻ marker. A player
        listing at their own price is not the app reviewing anything, so it writes only the ask
        and leaves the suggestion — and the plan behind it — exactly as it was.

        Never cleared when the offer ends. Cancelling a listing does not un-choose the price the
        player picked, and the alternative on a part-sold position that stays "Partially sold"
        is falling back to the suggestion, which is the stale number this field exists to stop
        being read as an ask. The next listing overwrites it.
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
        """Claim a GE offer opening or cancellation, returning False if it already applied.

        Claimed before the event is applied rather than after, so a crash in between costs one
        event instead of replaying it — the same order ``add_synced_trades`` and its fills use.
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

        ``since`` bounds the read to recent history for callers that only ever look at a
        short window (see ``NOT_BEFORE_SAFETY_MARGIN`` for why it is deliberately loose).
        Without it this loads every event ever imported, plus every one of their items —
        fine for the activity feed, which shows exactly that, and steadily more expensive
        for anything on a timer.

        ``account_hash``, unlike the same filter on ``list_tracked``, is exact: every synced
        trade carries a real character it happened on, so there is no untagged case to fall
        back into.
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

    def save_loadout_snapshot(self, snapshot: LoadoutSnapshot) -> None:
        """Replace any previous snapshot for this account — only the latest full state
        matters for that — and append its total value to the net-worth history, which is
        the one part of a snapshot worth keeping every reading of."""

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
