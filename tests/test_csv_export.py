"""The CSV export is a data backup, so its content has to be exact and complete."""

from __future__ import annotations

import csv
import io

from osrs_toolkit.csv_export import journal_csv
from osrs_toolkit.journal import BuyFill, SaleFill, TrackedTrade, TradeRecord


def _rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def _position(**overrides: object) -> TrackedTrade:
    defaults: dict[str, object] = {
        "position_id": 1,
        "created_at": "2026-08-10T12:00:00+00:00",
        "item_id": 1234,
        "item_name": "Dragon bones",
        "quantity": 100,
        "target_buy": 2_000,
        "target_sell": 2_500,
        "actual_buy": None,
        "actual_sell": None,
        "status": "Pending buy",
        "strategy": "Balanced (1–4h)",
    }
    defaults.update(overrides)
    return TrackedTrade(**defaults)


def test_header_matches_the_documented_columns() -> None:
    content = journal_csv([], [])
    header = content.splitlines()[0].split(",")
    assert header == [
        "Date",
        "Status",
        "Item",
        "Quantity",
        "Buy target",
        "Actual buy (avg)",
        "Sell target",
        "Actual sell (avg)",
        "Strategy",
        "Profit (gp)",
        "Profit is realized",
    ]


def test_a_position_with_no_fills_exports_only_its_targets() -> None:
    (row,) = _rows(journal_csv([_position()], []))

    assert row["Date"] == "2026-08-10"
    assert row["Status"] == "Pending buy"
    assert row["Actual buy (avg)"] == ""
    assert row["Actual sell (avg)"] == ""
    assert row["Profit is realized"] == "no"


def test_a_completed_position_exports_its_realized_profit() -> None:
    """Buy 100 @ 2,000, sell @ 2,500 (50gp tax): 100 x 450 = 45,000 gp, and it is realized."""
    position = _position(
        status="Completed",
        actual_buy=2_000,
        buy_fills=(BuyFill(1, 1, 100, 2_000),),
        sale_fills=(SaleFill(1, 1, 100, 2_500),),
    )

    (row,) = _rows(journal_csv([position], []))

    assert row["Actual buy (avg)"] == "2000"
    assert row["Actual sell (avg)"] == "2500"
    assert row["Profit (gp)"] == "45000"
    assert row["Profit is realized"] == "yes"


def test_a_partial_sale_exports_realized_profit_for_what_actually_sold() -> None:
    position = _position(
        status="Partially sold",
        actual_buy=2_000,
        quantity=100,
        buy_fills=(BuyFill(1, 1, 100, 2_000),),
        sale_fills=(SaleFill(1, 1, 40, 2_500),),
    )

    (row,) = _rows(journal_csv([position], []))

    assert row["Profit is realized"] == "yes"
    assert row["Actual sell (avg)"] == "2500"


def test_manual_trades_are_included_with_no_strategy() -> None:
    record = TradeRecord(
        trade_id=1,
        recorded_at="2026-08-11T09:00:00+00:00",
        item_name="Yew logs",
        quantity=10,
        buy_price=300,
        sell_price=400,
    )

    (row,) = _rows(journal_csv([], [record]))

    assert row["Status"] == "Completed (manual)"
    assert row["Strategy"] == ""
    assert row["Profit is realized"] == "yes"
    assert row["Profit (gp)"] == str(record.profit)


def test_tracked_positions_and_manual_trades_both_appear() -> None:
    record = TradeRecord(2, "2026-08-11T09:00:00+00:00", "Yew logs", 10, 300, 400)

    rows = _rows(journal_csv([_position()], [record]))

    assert len(rows) == 2
    assert {row["Item"] for row in rows} == {"Dragon bones", "Yew logs"}


def test_a_supplies_position_exports_with_its_status() -> None:
    (row,) = _rows(journal_csv([_position(status="Supplies")], []))

    assert row["Status"] == "Supplies"
