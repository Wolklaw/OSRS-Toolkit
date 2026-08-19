from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from osrs_toolkit.journal import (
    JournalRepository,
    LoadoutItem,
    LoadoutSnapshot,
    OfferCancelled,
    OfferOpened,
    SyncedItem,
    SyncedTrade,
)

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 1_000_000
MAX_STATUS_BYTES = 16_384
MAX_EVENTS_PER_IMPORT = 500
# Events this build cannot interpret stay in the queue, so the scan has to be able to step over
# a backlog of them to reach usable ones — while still bounding how many files one pass opens.
MAX_EVENT_SCAN = 2_000
MAX_ITEMS_PER_SIDE = 56
MAX_LOADOUT_ITEMS = 1_200
MAX_SKILLS = 40
MAX_REJECTED_FILES = 200
MAX_OFFER_STATE_BYTES = 16_384
# The Grand Exchange has exactly 8 offer slots, members or not (F2P just has fewer usable
# ones) — never more, so this both sizes the dashboard and bounds a malformed state file.
GE_SLOT_COUNT = 8

_BUY_OFFER_STATES = frozenset({"BUYING", "BOUGHT", "CANCELLED_BUY"})
_SELL_OFFER_STATES = frozenset({"SELLING", "SOLD", "CANCELLED_SELL"})
#: Public because the dashboard flashes a slot the instant it lands in one of these:
#: the offer is over and its goods are still in the slot, waiting to be collected.
TERMINAL_OFFER_STATES = frozenset({"BOUGHT", "SOLD", "CANCELLED_BUY", "CANCELLED_SELL"})

ParsedEvent = SyncedTrade | LoadoutSnapshot | OfferOpened | OfferCancelled


class SyncEventError(ValueError):
    pass


class UnsupportedEventError(SyncEventError):
    """A structurally sound event this build has no way to interpret yet — a type or schema
    version from a newer plugin. Separate from a malformed event because the right response is
    the opposite: wait for a version that understands it rather than quarantine it."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0
    applied_to_tracked: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class RuneLiteConnectionStatus:
    detected: bool = False
    active: bool = False
    account_name: str | None = None
    account_hash: str | None = None
    player_trade_tracking: bool = False


@dataclass(frozen=True, slots=True)
class GEOfferSlot:
    """One of the account's 8 real Grand Exchange slots, read straight from the plugin's
    own bookkeeping file rather than reconstructed from a stream of past events — the same
    state it diffs new offers against to detect fills. ``state`` is a raw RuneLite
    ``GrandExchangeOfferState`` name (e.g. "BUYING", "BOUGHT", "CANCELLED_SELL")."""

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
        """Finished (bought/sold/cancelled) but still sitting in its slot uncollected — the
        plugin only clears a slot once the player actually collects it in-game."""
        return self.state in TERMINAL_OFFER_STATES

    @property
    def percent_filled(self) -> float:
        return self.quantity_filled / self.total_quantity * 100 if self.total_quantity else 0.0


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
    def __init__(self, sync_root: Path | None = None) -> None:
        self.sync_root = sync_root or Path.home() / ".runelite" / "osrs-toolkit"
        self.events_dir = self.sync_root / "events"
        self.rejected_dir = self.sync_root / "rejected"
        self.status_path = self.sync_root / "status.json"
        self.state_dir = self.sync_root / "state"

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

    def read_offer_state(self, account_hash: str) -> dict[int, GEOfferSlot]:
        """The account's 8 Grand Exchange slots right now, straight from the plugin's own
        fill-detection bookkeeping rather than reconstructed from a stream of past events —
        so a slot with an offer not yet filled at all still shows up correctly, which no
        combination of this app's own synced events could tell it on their own.

        A slot absent from the result is empty, not unknown: the plugin removes a slot from
        this file the moment the player collects it in-game, the same instant that slot
        would show empty in the real interface. Any read failure, or a single malformed
        slot, degrades to that slot reading empty rather than breaking the whole dashboard.
        Use ``read_placed_offers`` where an empty result has to mean something: this one
        cannot say whether the slots are empty or unreadable.
        """
        slots = self.read_placed_offers(account_hash)
        return {} if slots is None else slots

    def read_placed_offers(self, account_hash: str) -> dict[int, GEOfferSlot] | None:
        """The account's slots as ``read_offer_state`` reads them, or None when there is no
        state to read them from.

        An account whose slots are all empty and one whose state cannot be read both have no
        offers to show, and a caller that wants to say "nothing is placed for this item" has
        to tell them apart: an empty mapping means the Grand Exchange really is empty, None
        means there was nothing to judge against. Everything a slot is read with is
        unchanged — only the failure paths are answered differently.
        """
        safe_hash = re.sub(r"[^a-f0-9]", "", account_hash) or "unknown"
        path = self.state_dir / f"{safe_hash}.json"
        try:
            if path.is_symlink() or path.stat().st_size > MAX_OFFER_STATE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        slots: dict[int, GEOfferSlot] = {}
        for key, value in payload.items():
            if len(slots) >= GE_SLOT_COUNT:
                break
            parsed = _parse_offer_slot(key, value)
            if parsed is not None:
                slots[parsed.slot] = parsed
        return slots

    def import_pending(
        self,
        repository: JournalRepository,
        suggested_sell_prices: Mapping[int, int] | None = None,
    ) -> ImportResult:
        """Import queued RuneLite events into the journal.

        ``suggested_sell_prices`` maps item_id to the app's current passive sell target
        (``ranking.offer_targets``), so a buy fill or offer with no pre-tracked plan can seed
        a real profit estimate instead of one that reads as guaranteed break-even. Omit it (or
        leave an item out of it) when no live market snapshot is available yet; positions are
        still created, just without that head start.
        """
        suggested_sell_prices = suggested_sell_prices or {}
        if not self.events_dir.exists():
            return ImportResult()
        rejected = skipped = scanned = 0
        parsed: list[tuple[Path, ParsedEvent]] = []
        for path in sorted(self.events_dir.glob("*.json")):
            if len(parsed) + rejected >= MAX_EVENTS_PER_IMPORT or scanned >= MAX_EVENT_SCAN:
                break
            scanned += 1
            try:
                if path.is_symlink() or path.stat().st_size > MAX_EVENT_BYTES:
                    raise SyncEventError("Unsafe event file")
                payload = json.loads(path.read_text(encoding="utf-8"))
                parsed_event = parse_sync_event(payload)
            except UnsupportedEventError:
                # The plugin updates itself through the Plugin Hub while this app is updated by
                # hand, so a plugin queuing event types this build has never heard of is the
                # normal direction for the two to drift apart — not corruption. Leave the file
                # queued and a later version imports it; the plugin's own 30-day prune clears it
                # if that version never arrives. Skipping does not spend the per-pass budget, so
                # a backlog of them cannot starve the events this build does understand.
                skipped += 1
                continue
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
            parsed.append((path, parsed_event))

        # An offer's events only make sense replayed in the order they happened: the offer has
        # to open before its fills land on it, and be cancelled only after. Queue file names are
        # random UUIDs, so sort by event time, and break ties by lifecycle order for events the
        # plugin wrote in the same instant (a final fill and the cancellation that followed it).
        parsed.sort(key=lambda pair: (_event_instant(pair[1]), _lifecycle_rank(pair[1]), pair[0].name))

        # One connection for the whole batch instead of one per event.
        trades = [(path, event) for path, event in parsed if isinstance(event, SyncedTrade)]
        results = repository.add_synced_trades([trade for _path, trade in trades])
        was_imported = {
            path: imported for (path, _trade), imported in zip(trades, results, strict=True)
        }

        imported = duplicates = applied_to_tracked = 0
        for path, event in parsed:
            if isinstance(event, SyncedTrade):
                if was_imported[path]:
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
            elif not repository.claim_offer_event(event.event_id):
                duplicates += 1
            else:
                # Not a trade — no coins or items moved at the moment an offer opens or is
                # cancelled, so there is nothing to record in the synced-trade activity log.
                # Only the Journal position needs to know.
                imported += 1
                if self._apply_offer_lifecycle(repository, event, suggested_sell_prices):
                    applied_to_tracked += 1
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Every event this pass applied is recorded under its own ID, so keeping the
                # queue file costs a second look at it rather than a second application.
                pass

        self._prune_rejected()
        return ImportResult(
            imported=imported,
            duplicates=duplicates,
            rejected=rejected,
            applied_to_tracked=applied_to_tracked,
            skipped=skipped,
        )

    def _prune_rejected(self) -> None:
        """Rejected events are kept so a malformed one can be looked at, not replayed. Cap the
        directory so an event the plugin keeps producing and this app keeps refusing — an
        oversized bank snapshot, say — cannot fill the disk one copy at a time."""
        try:
            files = sorted(
                self.rejected_dir.glob("*.invalid"), key=lambda path: path.stat().st_mtime
            )
        except OSError:
            return
        for path in files[: max(0, len(files) - MAX_REJECTED_FILES)]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

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
                )
            else:
                touched = repository.apply_offer_cancelled(
                    event.item_id, event.side, event.total_quantity, event.offer_price
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
        if not isinstance(total_quantity, int) or isinstance(total_quantity, bool) or total_quantity <= 0:
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
            )
        except ValueError:
            # A matching invariant (e.g. a stale quantity) failed; leave it for manual review
            # rather than letting one bad match break the rest of the import pass.
            return False
        return position_id is not None


def _event_instant(event: ParsedEvent) -> datetime:
    """When the event happened, as a comparable instant. Every timestamp reaching here has
    already been validated as ISO-8601, but the plugin's writer omits fields that are zero, so
    text order is not time order — and a value without a zone can't be compared with one that
    has it, hence the UTC default."""
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
        # Missing or non-numeric is malformed, not merely unfamiliar.
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
        return _parse_ge_fill(
            event_id, occurred_at, account_hash, account_name, body
        )
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
    return _parse_loadout_snapshot(occurred_at, account_hash, account_name, body)


def _parse_offer_lifecycle(
    event_class: type[OfferOpened | OfferCancelled],
    event_id: str,
    occurred_at: str,
    account_hash: str,
    account_name: str,
    body: dict[str, object],
) -> OfferOpened | OfferCancelled:
    """Opening and cancelling carry the same fields — the desktop app matches a cancellation to
    the position its opening created by the offer's own size and price — so they parse alike.

    Only an opening carries ``restored``, and only from plugin builds that know to send it; a
    missing or non-boolean value reads as False, which is the assumption every build made
    before the flag existed.
    """
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
    # Verified against live sells (2026-08-15): the plugin's coins come from RuneLite's
    # GrandExchangeOffer.getSpent(), which is the gross value traded — GE tax is not taken out
    # of it. Ten sells across five items each recorded exactly their listed price, and none
    # recorded less, which could not happen if tax had already been deducted. So this stays a
    # gross unit price, and the tax comes off once downstream in TrackedTrade.realized_profit
    # via ge_tax(). Do not subtract tax here as well; that would double-count it.
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
    return LoadoutSnapshot(
        account_hash=account_hash,
        account_name=account_name,
        captured_at=occurred_at,
        equipment=tuple(equipment),
        inventory=tuple(inventory),
        bank=tuple(bank),
        skills=skills,
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


def _parse_offer_slot(key: object, value: object) -> GEOfferSlot | None:
    """A single slot entry from the plugin's offer-state file. Returns None on any
    validation failure rather than raising, so one malformed slot degrades to reading
    empty instead of blanking the whole dashboard."""
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
