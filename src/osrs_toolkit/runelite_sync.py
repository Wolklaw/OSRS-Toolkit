from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from osrs_toolkit.journal import JournalRepository, SyncedItem, SyncedTrade

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 1_000_000
MAX_STATUS_BYTES = 16_384
MAX_EVENTS_PER_IMPORT = 500
MAX_ITEMS_PER_SIDE = 56


class SyncEventError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0


@dataclass(frozen=True, slots=True)
class RuneLiteConnectionStatus:
    detected: bool = False
    active: bool = False
    account_name: str | None = None
    account_hash: str | None = None
    player_trade_tracking: bool = False


class RuneLiteSyncImporter:
    def __init__(self, sync_root: Path | None = None) -> None:
        self.sync_root = sync_root or Path.home() / ".runelite" / "osrs-toolkit"
        self.events_dir = self.sync_root / "events"
        self.acks_dir = self.sync_root / "acks"
        self.rejected_dir = self.sync_root / "rejected"
        self.status_path = self.sync_root / "status.json"

    @property
    def plugin_detected(self) -> bool:
        return self.connection_status().detected

    @property
    def plugin_active(self) -> bool:
        return self.connection_status().active

    def connection_status(self) -> RuneLiteConnectionStatus:
        detected = self.sync_root.exists()
        if not detected:
            return RuneLiteConnectionStatus()
        try:
            if self.status_path.is_symlink() or self.status_path.stat().st_size > MAX_STATUS_BYTES:
                return RuneLiteConnectionStatus(detected=True)
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
                return RuneLiteConnectionStatus(detected=True)
            fresh = time.time() - self.status_path.stat().st_mtime < 30
            account_name = payload.get("account_name")
            if (
                not isinstance(account_name, str)
                or not account_name.strip()
                or account_name == "Not logged in"
                or len(account_name.strip()) > 32
            ):
                account_name = None
            else:
                account_name = account_name.strip()
            account_hash = payload.get("account_hash")
            if not isinstance(account_hash, str) or len(account_hash) > 64:
                account_hash = None
            return RuneLiteConnectionStatus(
                detected=True,
                active=fresh and payload.get("active") is True,
                account_name=account_name,
                account_hash=account_hash,
                player_trade_tracking=payload.get("player_trade_tracking") is True,
            )
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return RuneLiteConnectionStatus(detected=True)

    def import_pending(self, repository: JournalRepository) -> ImportResult:
        if not self.events_dir.exists():
            return ImportResult()
        self.acks_dir.mkdir(parents=True, exist_ok=True)
        rejected = 0
        paths = sorted(self.events_dir.glob("*.json"))[:MAX_EVENTS_PER_IMPORT]
        parsed: list[tuple[Path, SyncedTrade]] = []
        for path in paths:
            try:
                if path.is_symlink() or path.stat().st_size > MAX_EVENT_BYTES:
                    raise SyncEventError("Unsafe event file")
                payload = json.loads(path.read_text(encoding="utf-8"))
                trade = parse_sync_event(payload)
            except (json.JSONDecodeError, SyncEventError, TypeError, KeyError, ValueError):
                rejected += 1
                self.rejected_dir.mkdir(parents=True, exist_ok=True)
                destination = self.rejected_dir / f"{path.stem}-{uuid.uuid4().hex[:8]}.invalid"
                try:
                    shutil.move(str(path), str(destination))
                except OSError:
                    pass
                continue
            except OSError:
                # A partially written or temporarily locked file is not invalid. Leave it in
                # the queue so the next import pass can retry it.
                continue
            parsed.append((path, trade))

        # One connection for the whole batch instead of one per event.
        results = repository.add_synced_trades([trade for _path, trade in parsed])
        imported = duplicates = 0
        for (path, trade), was_imported in zip(parsed, results, strict=True):
            if was_imported:
                imported += 1
            else:
                duplicates += 1
            try:
                self._acknowledge(trade.event_id)
                path.unlink(missing_ok=True)
            except OSError:
                # The database commit is durable and event IDs are idempotent. Keeping the
                # queue file lets a later pass finish the acknowledgement safely.
                pass
        return ImportResult(imported=imported, duplicates=duplicates, rejected=rejected)

    def _acknowledge(self, event_id: str) -> None:
        destination = self.acks_dir / f"{event_id}.ack"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
        temporary.replace(destination)


def parse_sync_event(payload: object) -> SyncedTrade:
    if not isinstance(payload, dict):
        raise SyncEventError("Event must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SyncEventError("Unsupported schema version")
    event_id = _uuid_text(payload.get("event_id"))
    event_type = _text(payload.get("event_type"), "event_type", 32)
    if event_type not in {"ge_fill", "player_trade"}:
        raise SyncEventError("Unsupported event type")
    occurred_at = _timestamp(payload.get("occurred_at"))
    account = payload.get("account")
    if not isinstance(account, dict):
        raise SyncEventError("Missing account")
    account_hash = _text(account.get("hash"), "account hash", 64)
    account_name = _text(account.get("name"), "account name", 32)
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise SyncEventError("Missing payload")
    if event_type == "ge_fill":
        return _parse_ge_fill(
            event_id, occurred_at, account_hash, account_name, body
        )
    return _parse_player_trade(event_id, occurred_at, account_hash, account_name, body)


def _parse_ge_fill(
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> SyncedTrade:
    side = _text(body.get("side"), "side", 8).lower()
    if side not in {"buy", "sell"}:
        raise SyncEventError("Invalid GE side")
    quantity = _positive_int(body.get("quantity"), "quantity")
    coins = _positive_int(body.get("coins"), "coins")
    item_id = _positive_int(body.get("item_id"), "item id")
    item_name = _text(body.get("item_name"), "item name", 128)
    unit_price = coins // quantity
    item = SyncedItem(
        flow="received" if side == "buy" else "given",
        item_id=item_id,
        item_name=item_name,
        quantity=quantity,
        unit_value=unit_price,
    )
    money = SyncedItem(
        flow="given" if side == "buy" else "received",
        item_id=995,
        item_name="Coins",
        quantity=coins,
        unit_value=1,
    )
    metadata = {
        key: body[key]
        for key in ("offer_id", "offer_slot", "offer_price", "offer_state")
        if key in body
    }
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="ge_fill",
        account_hash=account_hash,
        account_name=account_name,
        counterparty=None,
        direction=side,
        metadata=metadata,
        items=(money, item),
    )


def _parse_player_trade(
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> SyncedTrade:
    counterparty = _text(body.get("counterparty"), "counterparty", 32)
    given = _items(body.get("given"), "given")
    received = _items(body.get("received"), "received")
    if not given and not received:
        raise SyncEventError("Player trade is empty")
    return SyncedTrade(
        event_id=event_id,
        occurred_at=occurred_at,
        event_type="player_trade",
        account_hash=account_hash,
        account_name=account_name,
        counterparty=counterparty,
        direction="exchange",
        metadata={},
        items=tuple(given + received),
    )


def _items(value: object, flow: str) -> list[SyncedItem]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS_PER_SIDE:
        raise SyncEventError(f"Invalid {flow} items")
    result: list[SyncedItem] = []
    for item in value:
        if not isinstance(item, dict):
            raise SyncEventError("Invalid trade item")
        result.append(
            SyncedItem(
                flow=flow,
                item_id=_positive_int(item.get("item_id"), "item id"),
                item_name=_text(item.get("item_name"), "item name", 128),
                quantity=_positive_int(item.get("quantity"), "quantity"),
                unit_value=_nonnegative_int(item.get("unit_value"), "unit value"),
            )
        )
    return result


def _uuid_text(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SyncEventError("Invalid event id") from exc


def _timestamp(value: object) -> str:
    text = _text(value, "occurred_at", 64)
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise SyncEventError("Invalid event time") from exc
    return text


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise SyncEventError(f"Invalid {field}")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    parsed = _nonnegative_int(value, field)
    if parsed == 0:
        raise SyncEventError(f"Invalid {field}")
    return parsed


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SyncEventError(f"Invalid {field}")
    return value
