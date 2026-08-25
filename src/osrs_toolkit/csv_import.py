"""Read back a Trade Journal CSV, independent of Qt.

The counterpart to :mod:`osrs_toolkit.csv_export`, which has existed on its own for a while --
an export you cannot import is a backup you cannot restore from, which is most of the reason
to keep one.

**Only completed trades come back.** The export writes two kinds of row: manual trades, and
tracked positions with whatever the plan and the fills said at the time. A position is a live
thing -- it owns fills, a listed price, a strategy, a status that moves -- and rebuilding one
from the flattened row the export produced would invent detail the file does not carry. A
completed trade is just an item, a quantity, and the two prices, which the file does carry
exactly. So rows that finished are imported and rows still in flight are reported as skipped
rather than half-restored into something that looks like a position and is not.

Parsing never touches a journal. :func:`parse_journal_csv` hands back what it found and what
it could not use, so a caller can tell somebody what is about to happen before it happens --
which matters more than usual here, because the obvious thing to do with this is replace a
journal with it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

#: The export's own header. Matched loosely -- by name, not position -- so a file somebody
#: opened in Excel and saved again, with columns reordered or extras bolted on, still reads.
REQUIRED_COLUMNS = ("Item", "Quantity", "Actual buy (avg)", "Actual sell (avg)")

#: Written by ``csv_export`` for a row that was a manual trade rather than a position.
MANUAL_STATUS = "Completed (manual)"


class CsvImportError(ValueError):
    """The file is not a journal export at all -- unreadable, or missing the columns."""


@dataclass(frozen=True, slots=True)
class ImportedTrade:
    item_name: str
    quantity: int
    buy_price: int
    sell_price: int


@dataclass(slots=True)
class ParsedImport:
    trades: list[ImportedTrade] = field(default_factory=list)
    #: One line per row that could not be imported, already phrased for a person to read.
    skipped: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.trades) + len(self.skipped)


def _clean_number(raw: str) -> int | None:
    """Read a price or quantity the way a spreadsheet might have left it.

    Thousands separators and a stray currency symbol are what a person gets after opening the
    export in Excel and saving it again; refusing those would reject files this app wrote.
    """
    text = (raw or "").strip().replace(",", "").replace("gp", "").replace("$", "").strip()
    if not text:
        return None
    try:
        # float first so "1500.0" survives -- Excel writes integers back that way.
        return int(float(text))
    except ValueError:
        return None


def parse_journal_csv(content: str) -> ParsedImport:
    """Read a journal CSV. Raises :class:`CsvImportError` if it is not one."""
    # utf-8-sig is what the export writes; a caller that already decoded may leave the mark.
    text = content.lstrip("﻿")
    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
    except csv.Error as exc:
        raise CsvImportError(f"Could not read that file as CSV: {exc}") from exc

    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise CsvImportError(
            "That does not look like a Trade Journal export. Missing column(s): "
            + ", ".join(missing)
        )

    parsed = ParsedImport()
    for number, row in enumerate(reader, start=2):  # start=2: row 1 is the header
        item = (row.get("Item") or "").strip()
        if not item:
            parsed.skipped.append(f"Row {number}: no item name")
            continue
        quantity = _clean_number(row.get("Quantity", ""))
        buy = _clean_number(row.get("Actual buy (avg)", ""))
        sell = _clean_number(row.get("Actual sell (avg)", ""))
        if quantity is None or quantity <= 0:
            parsed.skipped.append(f"Row {number} ({item}): quantity is missing or not a number")
            continue
        if buy is None or sell is None:
            # The ordinary case for an open position: the export leaves the actual columns
            # blank until it has really bought and really sold.
            status = (row.get("Status") or "").strip() or "still open"
            parsed.skipped.append(f"Row {number} ({item}): {status}, so it has no final prices")
            continue
        parsed.trades.append(
            ImportedTrade(
                item_name=item[:100],
                quantity=quantity,
                buy_price=buy,
                sell_price=sell,
            )
        )
    return parsed


def summarise(parsed: ParsedImport, *, replacing: bool, existing: int) -> str:
    """One paragraph a confirmation dialog can show before anything is written.

    Spells out the destructive half rather than leaving it to the button label: replacing is
    the option somebody picks by accident, and the number it would delete is the fact that
    stops them.
    """
    lines = [f"Found {len(parsed.trades)} completed trade(s) in the file."]
    if parsed.skipped:
        lines.append(
            f"{len(parsed.skipped)} row(s) cannot be imported -- open positions keep their "
            "plan and their fills, which a CSV does not carry."
        )
    if replacing:
        lines.append(
            f"\nReplacing DELETES the {existing} manually recorded trade(s) already in this "
            "journal, then imports the file. This cannot be undone."
        )
    else:
        lines.append(
            f"\nAdding keeps the {existing} trade(s) already here and appends the file's on "
            "top. Anything the file repeats will appear twice."
        )
    return "\n".join(lines)
