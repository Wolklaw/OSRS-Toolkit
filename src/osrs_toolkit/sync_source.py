from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAX_EVENT_BYTES = 1_000_000
MAX_STATUS_BYTES = 16_384
MAX_OFFER_STATE_BYTES = 16_384
MAX_OFFER_SCREEN_BYTES = 4_096
MAX_REJECTED_FILES = 200

LOCAL_STATUS_FRESH_SECONDS = 30

USER_AGENT = "OSRS-Toolkit/1.0 (+https://runescope.app)"

HTTP_TIMEOUT_SECONDS = 20

STATE_CACHE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class RuneLiteConnectionStatus:
    detected: bool = False
    active: bool = False
    account_name: str | None = None
    account_hash: str | None = None
    player_trade_tracking: bool = False
    source_reachable: bool = True
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class PendingSyncEvent:
    handle: str
    payload: object | None
    raw: bytes = field(default=b"", repr=False)


class SyncSource(Protocol):
    def status_payload(self) -> tuple[bool, dict | None, bool]:
        pass

    def offer_state_payload(self, account_hash: str) -> dict | None: ...

    def offer_screen_payload(self, account_hash: str) -> dict | None: ...

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        pass

    def collected(self, handles: list[str]) -> None:
        pass

    def quarantine(self, event: PendingSyncEvent) -> None:
        pass

    def housekeeping(self) -> None:
        pass


class LocalFileSource:
    def __init__(self, sync_root: Path | None = None) -> None:
        self.sync_root = sync_root or Path.home() / ".runelite" / "osrs-toolkit"
        self.events_dir = self.sync_root / "events"
        self.rejected_dir = self.sync_root / "rejected"
        self.status_path = self.sync_root / "status.json"
        self.state_dir = self.sync_root / "state"

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.sync_root.exists():
            return (False, None, False)
        try:
            if self.status_path.is_symlink() or self.status_path.stat().st_size > MAX_STATUS_BYTES:
                return (True, None, False)
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            fresh = time.time() - self.status_path.stat().st_mtime < LOCAL_STATUS_FRESH_SECONDS
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return (True, None, False)
        return (True, payload if isinstance(payload, dict) else None, fresh)

    def offer_state_payload(self, account_hash: str) -> dict | None:
        return self._read_state(f"{_safe_hash(account_hash)}.json", MAX_OFFER_STATE_BYTES)

    def offer_screen_payload(self, account_hash: str) -> dict | None:
        return self._read_state(f"{_safe_hash(account_hash)}-screen.json", MAX_OFFER_SCREEN_BYTES)

    def _read_state(self, name: str, maximum: int) -> dict | None:
        path = self.state_dir / name
        try:
            if path.is_symlink() or path.stat().st_size > maximum:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        if not self.events_dir.exists():
            return
        scanned = 0
        for path in sorted(self.events_dir.glob("*.json")):
            if scanned >= scan_limit:
                return
            scanned += 1
            try:
                if path.is_symlink() or path.stat().st_size > MAX_EVENT_BYTES:
                    yield PendingSyncEvent(path.name, None)
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            try:
                yield PendingSyncEvent(path.name, json.loads(raw), raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                yield PendingSyncEvent(path.name, None, raw)

    def collected(self, handles: list[str]) -> None:
        for handle in handles:
            try:
                (self.events_dir / handle).unlink(missing_ok=True)
            except OSError:
                pass

    def quarantine(self, event: PendingSyncEvent) -> None:
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(event.handle).stem
        destination = self.rejected_dir / f"{stem}-{uuid.uuid4().hex[:8]}.invalid"
        try:
            shutil.move(str(self.events_dir / event.handle), str(destination))
        except OSError:
            pass

    def housekeeping(self) -> None:
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


class HttpSyncSource:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._cached: tuple[float, str, dict | None] | None = None

    def _call(self, method: str, path: str, body: object | None = None) -> object | None:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", USER_AGENT)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return json.loads(response.read() or b"null")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
            return None

    def state_payload(self, account_hash: str = "") -> dict | None:
        if not self.base_url or not self.token:
            return None
        now = time.monotonic()
        cached = self._cached
        if cached is not None:
            cached_at, cached_hash, payload = cached
            if cached_hash == account_hash and now - cached_at < STATE_CACHE_SECONDS:
                return payload
        fetched = self._call(
            "GET", f"/v1/state?account_hash={urllib.parse.quote(account_hash, safe='')}"
        )
        payload = fetched if isinstance(fetched, dict) else None
        self._cached = (now, account_hash, payload)
        return payload

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.base_url or not self.token:
            return (False, None, False)
        payload = self.state_payload()
        if payload is None:
            return (True, None, False)
        return (True, payload, bool(payload.get("active")))

    def known_accounts(self) -> list[dict]:
        payload = self._call("GET", "/v1/accounts")
        if not isinstance(payload, dict):
            return []
        accounts = payload.get("accounts")
        return accounts if isinstance(accounts, list) else []

    def offer_state_payload(self, account_hash: str) -> dict | None:
        payload = self.state_payload(account_hash)
        if payload is None:
            return None
        offers = payload.get("offers")
        return offers if isinstance(offers, dict) else None

    def offer_screen_payload(self, account_hash: str) -> dict | None:
        payload = self.state_payload(account_hash)
        if payload is None:
            return None
        screen = payload.get("screen")
        if not isinstance(screen, dict):
            return None
        stamped = dict(screen)
        stamped.setdefault("updated_at", payload.get("screen_updated_at"))
        return stamped

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        payload = self._call("GET", f"/v1/events?limit={min(scan_limit, 500)}")
        if not isinstance(payload, dict):
            return
        events = payload.get("events")
        if not isinstance(events, list):
            return
        for event in events:
            handle = event.get("event_id") if isinstance(event, dict) else None
            if not isinstance(handle, str) or not handle:
                continue
            yield PendingSyncEvent(handle, event)

    def collected(self, handles: list[str]) -> None:
        if handles:
            self._call("POST", "/v1/events/ack", {"event_ids": handles})

    def quarantine(self, event: PendingSyncEvent) -> None:
        self.collected([event.handle])

    def housekeeping(self) -> None:
        return


def _safe_hash(account_hash: str) -> str:
    cleaned = "".join(character for character in account_hash if character in "abcdef0123456789")
    return cleaned or "unknown"
