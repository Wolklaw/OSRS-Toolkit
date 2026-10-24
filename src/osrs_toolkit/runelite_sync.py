from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    NpcLootRecord,
    OfferCancelled,
    OfferOpened,
    PlayerDeathRecord,
    SyncedItem,
    SyncedTrade,
)
from osrs_toolkit.sync_source import (
    LocalFileSource,
    PendingSyncEvent,
    RuneLiteConnectionStatus,
    SyncSource,
)

__all__ = ["RuneLiteConnectionStatus"]

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 1_000_000
MAX_STATUS_BYTES = 16_384
MAX_EVENTS_PER_IMPORT = 500
MAX_EVENT_SCAN = 2_000
MAX_ITEMS_PER_SIDE = 56
MAX_LOADOUT_ITEMS = 1_200
MAX_SKILLS = 40
MAX_REJECTED_FILES = 200
MAX_OFFER_STATE_BYTES = 16_384
MAX_OFFER_SCREEN_BYTES = 4_096
OFFER_SCREEN_MAX_AGE_SECONDS = 45
GE_SLOT_COUNT = 8

_BUY_OFFER_STATES = frozenset({"BUYING", "BOUGHT", "CANCELLED_BUY"})
_SELL_OFFER_STATES = frozenset({"SELLING", "SOLD", "CANCELLED_SELL"})
FILLED_OFFER_STATES = frozenset({"BOUGHT", "SOLD"})
TERMINAL_OFFER_STATES = FILLED_OFFER_STATES | frozenset({"CANCELLED_BUY", "CANCELLED_SELL"})

ParsedEvent = (
    SyncedTrade | LoadoutSnapshot | OfferOpened | OfferCancelled | NpcLootRecord | PlayerDeathRecord
)


class SyncEventError(ValueError):
    pass


class UnsupportedEventError(SyncEventError):
    pass


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0
    applied_to_tracked: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class GEOfferSlot:
    slot: int
    item_id: int
    item_name: str
    offer_price: int
    total_quantity: int
    quantity_filled: int
    spent_gp: int
    state: str

    @property
    def side(self) -> str | None:
        if self.state in _BUY_OFFER_STATES:
            return "buy"
        if self.state in _SELL_OFFER_STATES:
            return "sell"
        return None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_OFFER_STATES

    @property
    def is_filled(self) -> bool:
        return self.state in FILLED_OFFER_STATES

    @property
    def percent_filled(self) -> float:
        return self.quantity_filled / self.total_quantity * 100 if self.total_quantity else 0.0


@dataclass(frozen=True, slots=True)
class GEOfferScreen:
    item_id: int
    item_name: str
    side: str | None

    @property
    def focused(self) -> bool:
        return self.item_id > 0


def ge_offer_status_label(state: str) -> str:
    return {
        "BUYING": "Buying",
        "SELLING": "Selling",
        "BOUGHT": "Bought — collect",
        "SOLD": "Sold — collect",
        "CANCELLED_BUY": "Cancelled — collect",
        "CANCELLED_SELL": "Cancelled — collect",
    }.get(state, state.title())


class RuneLiteSyncImporter:
    def __init__(self, sync_root: Path | None = None, *, source: SyncSource | None = None) -> None:
        self.source = source or LocalFileSource(sync_root)
        self.sync_root = getattr(self.source, "sync_root", None)

    @property
    def configured(self) -> bool:
        return getattr(self.source, "configured", True)

    @property
    def plugin_detected(self) -> bool:
        return self.connection_status().detected

    @property
    def plugin_active(self) -> bool:
        return self.connection_status().active

    def known_accounts(self) -> list[dict]:
        lister = getattr(self.source, "known_accounts", None)
        return lister() if callable(lister) else []

    def connection_status(self) -> RuneLiteConnectionStatus:
        detected, payload, fresh = self.source.status_payload()
        if not detected or not isinstance(payload, dict):
            last_error = getattr(self.source, "last_error", None) if detected else None
            return RuneLiteConnectionStatus(
                detected=detected, source_reachable=not detected, last_error=last_error
            )
        if payload.get("schema_version") not in (None, SCHEMA_VERSION):
            return RuneLiteConnectionStatus(detected=True)
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
            active=fresh and payload.get("active", True) is True,
            account_name=account_name,
            account_hash=account_hash,
            player_trade_tracking=payload.get("player_trade_tracking") is True,
        )

    def read_offer_state(self, account_hash: str) -> dict[int, GEOfferSlot]:
        slots = self.read_placed_offers(account_hash)
        return {} if slots is None else slots

    def read_placed_offers(self, account_hash: str) -> dict[int, GEOfferSlot] | None:
        payload = self.source.offer_state_payload(account_hash)
        if payload is None:
            return None
        slots: dict[int, GEOfferSlot] = {}
        for key, value in payload.items():
            if len(slots) >= GE_SLOT_COUNT:
                break
            parsed = _parse_offer_slot(key, value)
            if parsed is not None:
                slots[parsed.slot] = parsed
        return slots

    def read_offer_screen(self, account_hash: str) -> GEOfferScreen | None:
        payload = self.source.offer_screen_payload(account_hash)
        if payload is None:
            return None
        try:
            written_at = datetime.fromisoformat(_text(payload.get("updated_at"), "updated_at", 64))
        except (SyncEventError, ValueError):
            return None
        written_at = written_at.replace(tzinfo=UTC) if written_at.tzinfo is None else written_at
        if (datetime.now(UTC) - written_at).total_seconds() > OFFER_SCREEN_MAX_AGE_SECONDS:
            return None
        try:
            item_id = _positive_int(payload.get("item_id"), "item id")
            item_name = _text(payload.get("item_name"), "item name", 128)
        except SyncEventError:
            return GEOfferScreen(item_id=0, item_name="", side=None)
        side = payload.get("side")
        return GEOfferScreen(
            item_id=item_id,
            item_name=item_name,
            side=side if side in ("buy", "sell") else None,
        )

    def import_pending(
        self,
        repository: JournalRepository,
        suggested_sell_prices: Mapping[int, int] | None = None,
    ) -> ImportResult:
        suggested_sell_prices = suggested_sell_prices or {}
        rejected = skipped = 0
        parsed: list[tuple[PendingSyncEvent, ParsedEvent]] = []
        for pending in self.source.pending(MAX_EVENT_SCAN):
            if len(parsed) + rejected >= MAX_EVENTS_PER_IMPORT:
                break
            if pending.payload is None:
                rejected += 1
                self.source.quarantine(pending)
                continue
            try:
                parsed_event = parse_sync_event(pending.payload)
            except UnsupportedEventError:
                skipped += 1
                continue
            except (SyncEventError, TypeError, KeyError, ValueError):
                rejected += 1
                self.source.quarantine(pending)
                continue
            parsed.append((pending, parsed_event))

        parsed.sort(
            key=lambda pair: (_event_instant(pair[1]), _lifecycle_rank(pair[1]), pair[0].handle)
        )

        trades = [(pending, event) for pending, event in parsed if isinstance(event, SyncedTrade)]
        results = repository.add_synced_trades([trade for _path, trade in trades])
        was_imported = {
            pending.handle: imported
            for (pending, _trade), imported in zip(trades, results, strict=True)
        }

        imported = duplicates = applied_to_tracked = 0
        collected: list[str] = []
        for pending, event in parsed:
            if isinstance(event, SyncedTrade):
                if was_imported[pending.handle]:
                    imported += 1
                    if event.event_type == "ge_fill" and self._apply_ge_fill(
                        repository, event, suggested_sell_prices
                    ):
                        applied_to_tracked += 1
                else:
                    duplicates += 1
            elif isinstance(event, LoadoutSnapshot):
                repository.save_loadout_snapshot(event)
                imported += 1
            elif isinstance(event, NpcLootRecord):
                if repository.add_npc_loot_event(event):
                    imported += 1
                else:
                    duplicates += 1
            elif isinstance(event, PlayerDeathRecord):
                if repository.add_player_death_event(event):
                    imported += 1
                else:
                    duplicates += 1
            elif not repository.claim_offer_event(event.event_id):
                duplicates += 1
            else:
                imported += 1
                if self._apply_offer_lifecycle(repository, event, suggested_sell_prices):
                    applied_to_tracked += 1
            collected.append(pending.handle)

        self.source.collected(collected)
        self.source.housekeeping()
        return ImportResult(
            imported=imported,
            duplicates=duplicates,
            rejected=rejected,
            applied_to_tracked=applied_to_tracked,
            skipped=skipped,
        )

    @staticmethod
    def _apply_offer_lifecycle(
        repository: JournalRepository,
        event: OfferOpened | OfferCancelled,
        suggested_sell_prices: Mapping[int, int],
    ) -> bool:
        try:
            if isinstance(event, OfferOpened):
                touched = repository.apply_offer_opened(
                    event.item_id,
                    event.item_name,
                    event.side,
                    event.total_quantity,
                    event.offer_price,
                    suggested_sell_prices.get(event.item_id),
                    event.restored,
                    account_hash=event.account_hash,
                    occurred_at=event.occurred_at,
                )
            else:
                touched = repository.apply_offer_cancelled(
                    event.item_id,
                    event.side,
                    event.total_quantity,
                    event.offer_price,
                    account_hash=event.account_hash,
                )
        except ValueError:
            return False
        return touched is not None

    @staticmethod
    def _apply_ge_fill(
        repository: JournalRepository,
        trade: SyncedTrade,
        suggested_sell_prices: Mapping[int, int],
    ) -> bool:
        item = trade.received[0] if trade.direction == "buy" else trade.given[0]
        total_quantity = trade.metadata.get("total_quantity")
        if (
            not isinstance(total_quantity, int)
            or isinstance(total_quantity, bool)
            or total_quantity <= 0
        ):
            total_quantity = None
        try:
            position_id = repository.apply_synced_ge_fill(
                item.item_id,
                item.item_name,
                trade.direction,
                item.quantity,
                item.unit_value,
                total_quantity=total_quantity,
                suggested_sell_price=suggested_sell_prices.get(item.item_id),
                account_hash=trade.account_hash,
                occurred_at=trade.occurred_at,
            )
        except ValueError:
            return False
        return position_id is not None


def _event_instant(event: ParsedEvent) -> datetime:
    raw = event.captured_at if isinstance(event, LoadoutSnapshot) else event.occurred_at
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _lifecycle_rank(event: ParsedEvent) -> int:
    if isinstance(event, OfferOpened):
        return 0
    if isinstance(event, OfferCancelled):
        return 2
    return 1


def parse_sync_event(payload: object) -> ParsedEvent:
    if not isinstance(payload, dict):
        raise SyncEventError("Event must be an object")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise SyncEventError("Invalid schema version")
    if schema_version != SCHEMA_VERSION:
        raise UnsupportedEventError("Unsupported schema version")
    event_id = _uuid_text(payload.get("event_id"))
    event_type = _text(payload.get("event_type"), "event_type", 32)
    if event_type not in {
        "ge_fill",
        "player_trade",
        "loadout_snapshot",
        "ge_offer_opened",
        "ge_offer_cancelled",
        "npc_loot",
        "player_death",
    }:
        raise UnsupportedEventError("Unsupported event type")
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
        return _parse_ge_fill(event_id, occurred_at, account_hash, account_name, body)
    if event_type == "player_trade":
        return _parse_player_trade(event_id, occurred_at, account_hash, account_name, body)
    if event_type == "ge_offer_opened":
        return _parse_offer_lifecycle(
            OfferOpened, event_id, occurred_at, account_hash, account_name, body
        )
    if event_type == "ge_offer_cancelled":
        return _parse_offer_lifecycle(
            OfferCancelled, event_id, occurred_at, account_hash, account_name, body
        )
    if event_type == "npc_loot":
        return _parse_npc_loot(event_id, occurred_at, account_hash, account_name, body)
    if event_type == "player_death":
        return _parse_player_death(event_id, occurred_at, account_hash, account_name, body)
    return _parse_loadout_snapshot(occurred_at, account_hash, account_name, body)


def _parse_offer_lifecycle(
    event_class: type[OfferOpened | OfferCancelled],
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> OfferOpened | OfferCancelled:
    side = _text(body.get("side"), "side", 8).lower()
    if side not in {"buy", "sell"}:
        raise SyncEventError("Invalid GE side")
    fields: dict[str, object] = {
        "event_id": event_id,
        "account_hash": account_hash,
        "account_name": account_name,
        "occurred_at": occurred_at,
        "side": side,
        "item_id": _positive_int(body.get("item_id"), "item id"),
        "item_name": _text(body.get("item_name"), "item name", 128),
        "offer_price": _nonnegative_int(body.get("offer_price"), "offer price"),
        "total_quantity": _positive_int(body.get("total_quantity"), "total quantity"),
    }
    if event_class is OfferOpened:
        fields["restored"] = body.get("restored") is True
    return event_class(**fields)  # type: ignore[arg-type]


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
        for key in ("offer_id", "offer_slot", "offer_price", "offer_state", "total_quantity")
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


def _parse_loadout_snapshot(
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> LoadoutSnapshot:
    equipment = _loadout_items(body.get("equipment"), "equipment")
    inventory = _loadout_items(body.get("inventory"), "inventory")
    bank = _loadout_items(body.get("bank"), "bank")
    skills_raw = body.get("skills")
    if not isinstance(skills_raw, dict) or len(skills_raw) > MAX_SKILLS:
        raise SyncEventError("Invalid skills")
    skills: dict[str, int] = {}
    for name, level in skills_raw.items():
        skills[_text(name, "skill name", 32)] = _nonnegative_int(level, "skill level")
    xp_raw = body.get("xp")
    xp: dict[str, int] = {}
    if xp_raw is not None:
        if not isinstance(xp_raw, dict) or len(xp_raw) > MAX_SKILLS:
            raise SyncEventError("Invalid xp")
        for name, amount in xp_raw.items():
            xp[_text(name, "skill name", 32)] = _nonnegative_int(amount, "skill xp")
    return LoadoutSnapshot(
        account_hash=account_hash,
        account_name=account_name,
        captured_at=occurred_at,
        equipment=tuple(equipment),
        inventory=tuple(inventory),
        bank=tuple(bank),
        skills=skills,
        xp=xp,
    )


def _loadout_items(value: object, field: str) -> list[LoadoutItem]:
    if not isinstance(value, list) or len(value) > MAX_LOADOUT_ITEMS:
        raise SyncEventError(f"Invalid {field}")
    result: list[LoadoutItem] = []
    for item in value:
        if not isinstance(item, dict):
            raise SyncEventError(f"Invalid {field} item")
        result.append(
            LoadoutItem(
                item_id=_positive_int(item.get("item_id"), "item id"),
                item_name=_text(item.get("item_name"), "item name", 128),
                quantity=_positive_int(item.get("quantity"), "quantity"),
                unit_value=_nonnegative_int(item.get("unit_value"), "unit value"),
            )
        )
    return result


def _parse_npc_loot(
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> NpcLootRecord:
    npc_name = _text(body.get("npc_name"), "npc name", 128)
    items = _loadout_items(body.get("items"), "items")
    if not items:
        raise SyncEventError("NPC loot is empty")
    return NpcLootRecord(
        event_id=event_id,
        occurred_at=occurred_at,
        account_hash=account_hash,
        account_name=account_name,
        npc_name=npc_name,
        items=tuple(items),
    )


def _parse_player_death(
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> PlayerDeathRecord:
    skulled = body.get("skulled") is True
    equipment = _loadout_items(body.get("equipment"), "equipment")
    inventory = _loadout_items(body.get("inventory"), "inventory")
    return PlayerDeathRecord(
        event_id=event_id,
        occurred_at=occurred_at,
        account_hash=account_hash,
        account_name=account_name,
        skulled=skulled,
        equipment=tuple(equipment),
        inventory=tuple(inventory),
    )


def _parse_offer_slot(key: object, value: object) -> GEOfferSlot | None:
    try:
        slot_index = int(str(key))
    except (TypeError, ValueError):
        return None
    if not 0 <= slot_index < GE_SLOT_COUNT or not isinstance(value, dict):
        return None
    try:
        return GEOfferSlot(
            slot=slot_index,
            item_id=_positive_int(value.get("itemId"), "item id"),
            item_name=_text(value.get("itemName"), "item name", 128),
            offer_price=_nonnegative_int(value.get("offerPrice"), "offer price"),
            total_quantity=_positive_int(value.get("totalQuantity"), "total quantity"),
            quantity_filled=_nonnegative_int(value.get("quantityFilled"), "quantity filled"),
            spent_gp=_nonnegative_int(value.get("spentGp"), "spent gp"),
            state=_text(value.get("state"), "state", 32),
        )
    except SyncEventError:
        return None


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
