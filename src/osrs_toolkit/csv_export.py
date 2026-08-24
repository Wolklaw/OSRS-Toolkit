"""Export the Trade Journal to CSV, independent of Qt.

Includes every tracked position and manual trade regardless of the current status/period
filter — an export is a backup, not a view of the filtered table.
"""

from __future__ import annotations

import csv
import io

from osrs_toolkit.journal import TrackedTrade, TradeRecord

_HEADER = [
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


def journal_csv(tracked: list[TrackedTrade], trades: list[TradeRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_HEADER)
    for trade in tracked:
        realized = trade.realized_profit
        writer.writerow(
            [
                trade.created_at[:10],
                trade.status,
                trade.item_name,
                trade.quantity,
                trade.target_buy,
                trade.average_buy_price if trade.average_buy_price is not None else "",
                trade.target_sell,
                trade.average_sell_price if trade.average_sell_price is not None else "",
                trade.strategy,
                realized if realized is not None else trade.estimated_profit,
                "yes" if realized is not None else "no",
            ]
        )
    for record in trades:
        writer.writerow(
            [
                record.recorded_at[:10],
                "Completed (manual)",
                record.item_name,
                record.quantity,
                record.buy_price,
                record.buy_price,
                record.sell_price,
                record.sell_price,
                "",
                record.profit,
                "yes",
            ]
        )
    return buffer.getvalue()
