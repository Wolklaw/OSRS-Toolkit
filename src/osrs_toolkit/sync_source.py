"""Where synced events come from: a local ``.runelite`` folder (older plugin builds) or the
sync web service (current plugin, per Plugin Hub rules). A source only handles transport —
fetching raw payloads and acknowledging them — parsing lives in ``runelite_sync``.
"""

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

# Local heartbeat re-stamps every 10s; 30s of silence means the client is gone. Doesn't apply
# to the web service, which answers this itself.
LOCAL_STATUS_FRESH_SECONDS = 30

# Cloudflare 403s the default urllib User-Agent, which looks like a bug in our code. Set
# explicitly to avoid that.
USER_AGENT = "OSRS-Toolkit/1.0 (+https://runescope.app)"

HTTP_TIMEOUT_SECONDS = 20

# How long a /v1/state fetch is cached — long enough that one page's several reads cost one
# request, short enough not to outlive the render.
STATE_CACHE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class RuneLiteConnectionStatus:
    detected: bool = False
    active: bool = False
    account_name: str | None = None
    account_hash: str | None = None
    player_trade_tracking: bool = False
    # Whether the source itself answered. False means unreachable (site down, etc), not
    # "plugin offline" — different message to show. Always true for the local folder.
    source_reachable: bool = True
    # What broke, for a source that can say (the website; a local folder never fails this
    # way). Only meaningful when source_reachable is False. Shown in the status text so a
    # recurring "unreachable" is something to report with a cause, not just a symptom.
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class PendingSyncEvent:
    """One event waiting to be imported, plus whatever the source needs to forget it again.

    ``payload`` is ``None`` when the source reached the event but couldn't parse it as JSON —
    treated as a rejection, not a retry, since reading it again fails the same way.
    """

    handle: str
    payload: object | None
    raw: bytes = field(default=b"", repr=False)


class SyncSource(Protocol):
    def status_payload(self) -> tuple[bool, dict | None, bool]:
        """(detected, payload, fresh) — whether a source exists, what it last said, and
        whether that was recent enough to believe."""

    def offer_state_payload(self, account_hash: str) -> dict | None: ...

    def offer_screen_payload(self, account_hash: str) -> dict | None: ...

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        """Events waiting, oldest first, produced lazily so an unreadable event doesn't spend
        the caller's per-pass budget and starve the readable ones behind it.
        """

    def collected(self, handles: list[str]) -> None:
        """Forget events that have been imported, or that will never be importable."""

    def quarantine(self, event: PendingSyncEvent) -> None:
        """Keep a malformed event somewhere it can be looked at, and out of the queue."""

    def housekeeping(self) -> None:
        """Run once per import pass, whether or not anything was imported."""


class LocalFileSource:
    """The original transport: a directory the plugin writes into.

    Kept because a player who has not updated their plugin still has events sitting in it, and
    because it is the only transport that works with no network at all.
    """

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
                # Partially written or briefly locked, not invalid — skip this pass, the next
                # one finds it finished.
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
                # Recorded under its own id, so a leftover file costs a second look, not a
                # second application.
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
        """Kept so a malformed event can be inspected, not replayed. Capped so a repeatedly
        rejected event (e.g. an oversized bank snapshot) can't fill the disk."""
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
    """The sync service.

    Events are acknowledged by id, not by cursor, so an event type this build doesn't
    recognize yet is skipped and still there after an update. Nothing here retries — a failed
    pass leaves the queue untouched.
    """

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
        """Everything the service knows about one character, in one request.

        Memoised briefly since status/slots/offer-box each used to fetch the same response
        separately. Keyed by account hash so a different character is a guaranteed miss, not a
        wrong answer.
        """
        if not self.base_url or not self.token:
            return None
        now = time.monotonic()
        cached = self._cached
        if cached is not None:
            cached_at, cached_hash, payload = cached
            if cached_hash == account_hash and now - cached_at < STATE_CACHE_SECONDS:
                return payload
        # Not _safe_hash: that coerces "" to "unknown" for a safe *filename*, which is
        # right for LocalFileSource but wrong here -- an empty account_hash is meaningful to
        # the sync service ("whichever character this pairing was last seen playing"), and
        # sending the literal string "unknown" instead asked about the placeholder the plugin
        # posts before a character resolves, whose last_seen freezes the moment a real one
        # does. That silently read every live pairing as idle forever after login.
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
            # Configured but unreachable — detected stays true so the app says "can't reach
            # the service", not "no plugin".
            return (True, None, False)
        return (True, payload, bool(payload.get("active")))

    def known_accounts(self) -> list[dict]:
        """Every character the service has seen for this pairing, newest first.

        Lives here rather than on ``SyncSource`` since the local file bridge has no such
        registry and shouldn't have to fake one.
        """
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
        # Service withholds a stale screen itself, but stamps updated_at anyway so the
        # downstream age check has something to read.
        stamped = dict(screen)
        stamped.setdefault("updated_at", payload.get("screen_updated_at"))
        return stamped

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        # One request, not a stream — the service caps what it returns, and an extra round
        # trip over a home connection costs more than a few extra events.
        payload = self._call("GET", f"/v1/events?limit={min(scan_limit, 500)}")
        if not isinstance(payload, dict):
            return
        events = payload.get("events")
        if not isinstance(events, list):
            return
        for event in events:
            handle = event.get("event_id") if isinstance(event, dict) else None
            if not isinstance(handle, str) or not handle:
                # Without an id there is nothing to acknowledge, so it could never be cleared.
                continue
            yield PendingSyncEvent(handle, event)

    def collected(self, handles: list[str]) -> None:
        if handles:
            self._call("POST", "/v1/events/ack", {"event_ids": handles})

    def quarantine(self, event: PendingSyncEvent) -> None:
        # Nothing to gain keeping it — the service would hand back the same unusable event
        # every pass, so acknowledge it away.
        self.collected([event.handle])

    def housekeeping(self) -> None:
        # The service prunes its own queue.
        return


def _safe_hash(account_hash: str) -> str:
    cleaned = "".join(character for character in account_hash if character in "abcdef0123456789")
    return cleaned or "unknown"
