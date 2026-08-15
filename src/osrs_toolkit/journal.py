from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from osrs_toolkit.ranking import ge_tax


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

    @property
    def estimated_profit(self) -> int:
        buy_price = self.actual_buy or self.buy_suggestion
        sell_price = self.sell_suggestion
        return (sell_price - buy_price - ge_tax(sell_price)) * self.remaining_quantity

    @property
    def buy_suggestion(self) -> int:
        return self.current_buy_suggestion or self.target_buy

    @property
    def sell_suggestion(self) -> int:
        return self.current_sell_suggestion or self.target_sell

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
        return (self.actual_buy or self.target_buy) * self.quantity


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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS synced_trades_occurred ON synced_trades(occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS synced_trade_items_event "
                "ON synced_trade_items(event_id)"
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
    ) -> int:
        with self._connect() as connection:
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                INSERT INTO tracked_trades (
                    created_at, item_id, item_name, quantity, target_buy, target_sell, status,
                    strategy, current_buy_suggestion, current_sell_suggestion,
                    suggestion_reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending buy', ?, ?, ?, ?)
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
                ),
            )
            return int(cursor.lastrowid)

    def list_tracked(self) -> list[TrackedTrade]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tracked_trades ORDER BY created_at DESC, position_id DESC"
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
                "SELECT quantity FROM tracked_trades WHERE position_id = ?", (position_id,)
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

            completed_at = (
                datetime.now(UTC).isoformat(timespec="seconds")
                if status in self._TERMINAL_STATUSES
                else None
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

    def delete_tracked(self, position_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM tracked_trades WHERE position_id = ?", (position_id,)
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

    def list_synced_trades(self, event_type: str | None = None) -> list[SyncedTrade]:
        query = "SELECT * FROM synced_trades"
        parameters: tuple[str, ...] = ()
        if event_type is not None:
            query += " WHERE event_type = ?"
            parameters = (event_type,)
        query += " ORDER BY occurred_at DESC, event_id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            item_rows = connection.execute(
                """
                SELECT * FROM synced_trade_items
                ORDER BY item_row_id ASC
                """
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
