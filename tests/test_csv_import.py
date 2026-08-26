"""Reading a Trade Journal CSV back in.

The round-trip test is the one that matters: the export has existed on its own for a while,
so the import is only worth anything if it reads what that actually writes.
"""

from __future__ import annotations

import pytest

from osrs_toolkit.csv_export import journal_csv
from osrs_toolkit.csv_import import CsvImportError, parse_journal_csv, summarise
from osrs_toolkit.journal import TradeRecord


def _record(name: str = "Whip", quantity: int = 2, buy: int = 100, sell: int = 150):
    return TradeRecord(
        trade_id=1,
        recorded_at="2026-08-01T00:00:00",
        item_name=name,
        quantity=quantity,
        buy_price=buy,
        sell_price=sell,
        sync_uid="uid",
    )


def test_what_the_export_writes_is_what_the_import_reads():
    content = journal_csv([], [_record(), _record("Bones", 10, 5, 9)])

    parsed = parse_journal_csv(content)

    assert parsed.skipped == []
    assert [(t.item_name, t.quantity, t.buy_price, t.sell_price) for t in parsed.trades] == [
        ("Whip", 2, 100, 150),
        ("Bones", 10, 5, 9),
    ]


def test_a_file_that_is_not_a_journal_export_is_refused():
    with pytest.raises(CsvImportError) as caught:
        parse_journal_csv("name,email\nSomebody,a@b.c\n")

    assert "Item" in str(caught.value)


def test_an_empty_file_is_refused_rather_than_read_as_zero_trades():
    """Silently importing nothing, especially in replace mode, would wipe a journal and
    report success."""
    with pytest.raises(CsvImportError):
        parse_journal_csv("")


def test_an_open_position_is_skipped_with_a_reason_rather_than_invented():
    content = (
        "Date,Status,Item,Quantity,Buy target,Actual buy (avg),Sell target,"
        "Actual sell (avg),Strategy,Profit (gp),Profit is realized\n"
        "2026-08-01,Buying,Whip,2,100,,150,,,0,no\n"
    )

    parsed = parse_journal_csv(content)

    assert parsed.trades == []
    assert len(parsed.skipped) == 1
    assert "Whip" in parsed.skipped[0]
    assert "Buying" in parsed.skipped[0]


def test_numbers_a_spreadsheet_reformatted_still_read():
    """Opening the export in Excel and saving it again is the ordinary way a file gets here."""
    content = (
        "Date,Status,Item,Quantity,Buy target,Actual buy (avg),Sell target,"
        "Actual sell (avg),Strategy,Profit (gp),Profit is realized\n"
        '2026-08-01,Completed (manual),Whip,"1,000",100,"1,500.0",150,"2,000",,0,yes\n'
    )

    parsed = parse_journal_csv(content)

    [trade] = parsed.trades
    assert (trade.quantity, trade.buy_price, trade.sell_price) == (1_000, 1_500, 2_000)


def test_columns_may_be_reordered_or_have_extras():
    content = "Item,Notes,Actual sell (avg),Quantity,Actual buy (avg)\nWhip,mine,150,2,100\n"

    [trade] = parse_journal_csv(content).trades

    assert (trade.item_name, trade.quantity, trade.buy_price, trade.sell_price) == (
        "Whip",
        2,
        100,
        150,
    )


def test_a_row_with_no_item_name_is_skipped():
    content = "Item,Quantity,Actual buy (avg),Actual sell (avg)\n,2,100,150\n"

    parsed = parse_journal_csv(content)

    assert parsed.trades == []
    assert "no item name" in parsed.skipped[0]


def test_a_nonsense_quantity_is_skipped_rather_than_imported_as_zero():
    content = "Item,Quantity,Actual buy (avg),Actual sell (avg)\nWhip,lots,100,150\n"

    parsed = parse_journal_csv(content)

    assert parsed.trades == []
    assert "quantity" in parsed.skipped[0]


def test_the_summary_names_what_replacing_would_delete():
    """The number is the thing that stops somebody picking Replace by accident."""
    parsed = parse_journal_csv(journal_csv([], [_record()]))

    replacing = summarise(parsed, replacing=True, existing=42)
    adding = summarise(parsed, replacing=False, existing=42)

    assert "DELETES the 42" in replacing
    assert "cannot be undone" in replacing
    assert "keeps the 42" in adding
