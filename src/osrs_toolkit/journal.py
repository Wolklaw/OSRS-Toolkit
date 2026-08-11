from __future__ import annotations

import os
import sqlite3
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

    @property
    def estimated_profit(self) -> int:
        return (self.target_sell - self.target_buy - ge_tax(self.target_sell)) * self.quantity

    @property
    def realized_profit(self) -> int | None:
        if self.status != "Completed" or self.actual_buy is None or self.actual_sell is None:
            return None
        return (self.actual_sell - self.actual_buy - ge_tax(self.actual_sell)) * self.quantity

    @property
    def invested(self) -> int:
        return (self.actual_buy or self.target_buy) * self.quantity


class JournalRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        if database_path is None:
            local_data = Path(os.getenv("LOCALAPPDATA", Path.home()))
            database_path = local_data / "OSRSToolkit" / "data" / "toolkit.db"
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

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
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tracked_trades (
                    created_at, item_id, item_name, quantity, target_buy, target_sell, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending buy')
                """,
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    item_id,
                    item_name,
                    quantity,
                    target_buy,
                    target_sell,
                ),
            )
            return int(cursor.lastrowid)

    def list_tracked(self) -> list[TrackedTrade]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tracked_trades ORDER BY created_at DESC, position_id DESC"
            ).fetchall()
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
            )
            for row in rows
        ]

    def update_tracked(
        self,
        position_id: int,
        status: str,
        actual_buy: int | None,
        actual_sell: int | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tracked_trades
                SET status = ?, actual_buy = ?, actual_sell = ?
                WHERE position_id = ?
                """,
                (status, actual_buy, actual_sell, position_id),
            )

    def delete_tracked(self, position_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM tracked_trades WHERE position_id = ?", (position_id,)
            )
