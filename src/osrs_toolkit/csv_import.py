from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

REQUIRED_COLUMNS = ("Item", "Quantity", "Actual buy (avg)", "Actual sell (avg)")

MANUAL_STATUS = "Completed (manual)"


class CsvImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportedTrade:
    item_name: str
    quantity: int
    buy_price: int
    sell_price: int


@dataclass(slots=True)
class ParsedImport:
    trades: list[ImportedTrade] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.trades) + len(self.skipped)


def _clean_number(raw: str) -> int | None:
    text = (raw or "").strip().replace(",", "").replace("gp", "").replace("$", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_journal_csv(content: str) -> ParsedImport:
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
    for number, row in enumerate(reader, start=2):
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
