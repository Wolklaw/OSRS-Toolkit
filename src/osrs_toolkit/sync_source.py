"""Where synced events come from.

The plugin used to hand its events over by writing files into ``.runelite``. The RuneLite
Plugin Hub will not accept a plugin that depends on an application installed on the same
machine, so it posts them to a web service instead — and this app has to be able to read from
either, because a queue of files written by an older plugin should not become unreadable the
day the newer one ships.

A source deals only in transport. It fetches raw payloads and says when something has been
taken; it does not know what a Grand Exchange offer is. Everything that gives those payloads
meaning stays in ``runelite_sync``, which is what keeps this module free to be swapped and
keeps the parsing tested once rather than once per transport.
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.error
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

#: A local heartbeat is re-stamped every ten seconds, so half a minute of silence means the
#: client is gone. The web service answers this question itself and this does not apply to it.
LOCAL_STATUS_FRESH_SECONDS = 30

#: Sent on every request to the sync service. Not politeness: Cloudflare turns away the generic
#: name Python's standard library sends by default, with a 403 that arrives looking exactly like
#: a bug in our own code. Saying who we are avoids being mistaken for something else.
USER_AGENT = "OSRS-Toolkit/1.0 (+https://runescope.app)"

HTTP_TIMEOUT_SECONDS = 20

#: How long one fetch of ``/v1/state`` is reused for. Long enough that the several slices of it
#: one page wants cost a single request, short enough that it cannot outlive the render asking.
STATE_CACHE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class RuneLiteConnectionStatus:
    detected: bool = False
    active: bool = False
    account_name: str | None = None
    account_hash: str | None = None
    player_trade_tracking: bool = False
    #: Whether the source itself answered. False means this app could not reach wherever it
    #: reads from -- which says nothing at all about whether the plugin is running, and is a
    #: different thing to tell somebody than "the plugin is offline". Always true for the
    #: local folder: a directory that exists has, by definition, already answered.
    source_reachable: bool = True


@dataclass(frozen=True, slots=True)
class PendingSyncEvent:
    """One event waiting to be imported, and whatever the source needs to forget it again.

    ``payload`` is ``None`` where the source could reach the event but not make JSON of it.
    That is a rejection rather than a retry: reading it again will fail the same way, and
    leaving it in place would block everything queued behind it.
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
        """Events waiting, oldest first, produced lazily.

        Lazily because the caller's budget is measured in events it could *use*, and one it
        cannot read yet does not spend any of it. A source that handed back a fixed batch would
        let a backlog of those fill the batch and starve the events that do work.
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
                # A partially written or briefly locked file is not invalid. Leaving it out of
                # this pass entirely means the next one finds it finished.
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
                # Every event applied this pass is recorded under its own id, so a file left
                # behind costs a second look at it rather than a second application.
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
        """Rejected events are kept so a malformed one can be looked at, not replayed. Capping
        the directory stops an event the plugin keeps producing and this app keeps refusing —
        an oversized bank snapshot, say — filling the disk one copy at a time."""
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

    Events are taken from a queue held for this pairing and acknowledged by id once imported,
    which is what lets this app skip an event type it does not recognise yet and still find it
    waiting after an update. Acknowledging by cursor would sweep those away unread.

    Nothing here retries. A pass that cannot reach the service imports nothing and acknowledges
    nothing, so the queue is untouched and the next pass sees exactly what this one did.
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

        Memoised for a moment because the callers below each want a different slice of the same
        answer, and a page that draws status, slots and the offer box together used to fetch
        the identical response three times. The window is short enough that nothing here can go
        stale within a render, and it is keyed by hash so asking about a different character is
        a guaranteed miss rather than a wrong answer.
        """
        if not self.base_url or not self.token:
            return None
        now = time.monotonic()
        cached = self._cached
        if cached is not None:
            cached_at, cached_hash, payload = cached
            if cached_hash == account_hash and now - cached_at < STATE_CACHE_SECONDS:
                return payload
        fetched = self._call("GET", f"/v1/state?account_hash={_safe_hash(account_hash)}")
        payload = fetched if isinstance(fetched, dict) else None
        self._cached = (now, account_hash, payload)
        return payload

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.base_url or not self.token:
            return (False, None, False)
        payload = self.state_payload()
        if payload is None:
            # Configured but unreachable. Detected stays true so the app can say "cannot reach
            # the service" rather than "no plugin", which are different problems to fix.
            return (True, None, False)
        return (True, payload, bool(payload.get("active")))

    def known_accounts(self) -> list[dict]:
        """Every character the service has ever seen for this pairing, newest first.

        A capability the local file bridge never had: it only ever tracked whichever character
        happened to be logged in when it last wrote a file, and had no registry of the others.
        Centralising state on the service is what makes "switch character" something a caller
        can offer at all, so this lives here rather than on ``SyncSource`` — a source that
        cannot support it should not have to fake an empty answer for it.
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
        # The service already withholds a screen too old to believe, but it stamps what it
        # returns so the age check downstream has something to read either way.
        stamped = dict(screen)
        stamped.setdefault("updated_at", payload.get("screen_updated_at"))
        return stamped

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        # One request rather than a stream: the service already caps what it returns, and a
        # second round trip to a home connection costs more than reading a few extra events.
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
        # Nowhere to put it and nothing gained by keeping it: the service would hand back the
        # same unusable event on every pass. Acknowledging drops it.
        self.collected([event.handle])

    def housekeeping(self) -> None:
        # The service prunes its own queue.
        return


def _safe_hash(account_hash: str) -> str:
    cleaned = "".join(character for character in account_hash if character in "abcdef0123456789")
    return cleaned or "unknown"
