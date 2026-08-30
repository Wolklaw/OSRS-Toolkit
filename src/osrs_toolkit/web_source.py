"""Reads live plugin state from the website instead of the plugin directly.

The plugin posts to the sync service, the website collects from it, and this app reads from
the website — nothing the plugin does reaches this machine directly. Payloads are relayed
unchanged so ``runelite_sync``'s existing parsers stay the one place that gives them meaning.

Standard library only, like every module here — no runtime dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

from osrs_toolkit.sync_source import (
    HTTP_TIMEOUT_SECONDS,
    USER_AGENT,
    PendingSyncEvent,
)

DEFAULT_BASE_URL = "https://runescope.app"

# Timeout for the "Check connection" button (vs. 20s for a background refresh) — someone's
# watching, so a bad address needs to answer quickly, not freeze the app.
INTERACTIVE_TIMEOUT_SECONDS = 6.0

# Timeout for the live-state poll, which runs on the GUI thread every few seconds. The 20s
# default belongs to a one-off request nobody is waiting on; here it is how long the window
# can sit frozen on a single stalled request, so it is kept just under the poll interval.
# A poll that times out costs one skipped tick, and the next one is 3 seconds away.
POLL_TIMEOUT_SECONDS = 2.5

# Collapses one refresh's status/slots/offer-box calls into a single request instead of
# three round trips.
STATE_CACHE_SECONDS = 2.0


def _describe_failure(error: Exception) -> str:
    """A short, human string for whatever ``urlopen`` raised.

    Every failure the caller sees looks the same ("nothing to draw, try next tick") — this is
    the one place that still knows which it actually was, so a recurring "Website unreachable"
    can be reported with a cause (DNS, timeout, a specific HTTP status) instead of just the
    symptom.
    """
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, TimeoutError):
        return "timed out"
    if isinstance(error, urllib.error.URLError):
        return str(error.reason) or "connection failed"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "reply wasn't valid JSON"
    return str(error) or type(error).__name__


class ToolkitWebError(Exception):
    """The website could be reached but refused, in a way worth telling the user about."""


class ToolkitWebClient:
    """Talks to one account's corner of the website, carrying a desktop credential.

    Minted on the website's profile page, pasted in here. Distinct from the plugin's pairing
    token (its write credential to the sync service) — a token that can also read the journal
    would be worse to leak.
    """

    def __init__(
        self, base_url: str | None = None, token: str = "", timeout: float | None = None
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or ""
        self.timeout = timeout or HTTP_TIMEOUT_SECONDS
        # What the last failed call died of, so "Website unreachable" is something to report
        # with a cause rather than just a symptom. None after a call that succeeded.
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def get(self, path: str, timeout: float | None = None, **params: str) -> object | None:
        return self._call("GET", path, params=params, timeout=timeout)

    def post(self, path: str, body: object) -> object | None:
        return self._call("POST", path, body=body)

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> object | None:
        """The response as JSON, or ``None`` for anything that did not clearly succeed.

        Every failure answers the same way on purpose — a dashboard refresh treats "site
        down", "token revoked", and "reply wasn't JSON" identically: nothing to draw, try next
        tick. ``ToolkitWebError`` is raised only where a caller asks to be told — see ``check``.

        ``timeout`` overrides the client's own for this one call, for a small request made on
        a timer where the client's timeout is sized for a large one.
        """
        if not self.configured:
            return None
        supplied = {key: value for key, value in (params or {}).items() if value}
        url = f"{self.base_url}{path}"
        if supplied:
            url = f"{url}?{urllib.parse.urlencode(supplied)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", USER_AGENT)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                result = json.loads(response.read() or b"null")
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
            OSError,
        ) as error:
            self.last_error = _describe_failure(error)
            return None
        self.last_error = None
        return result

    def check(self) -> str:
        """Confirm the credential works and return the account name it belongs to.

        Used by the settings dialog, where a person just typed something and wants a real
        answer, not a quiet retry.
        """
        if not self.configured:
            raise ToolkitWebError("Enter the website address and a desktop access token.")
        payload = self.get("/api/me")
        if not isinstance(payload, dict) or not payload.get("username"):
            raise ToolkitWebError(
                "That token was not accepted. Generate a new one on the website's Profile page."
            )
        return str(payload["username"])


class WebAppSource:
    """A :class:`~osrs_toolkit.sync_source.SyncSource` backed by the website.

    Relays live state (login, GE slots, offer box) from the sync service. Yields no pending
    events on purpose — the website already imported them into the journal this app mirrors;
    importing them again here would double them, and acknowledging would steal them from other
    viewers of the same account.
    """

    def __init__(self, client: ToolkitWebClient) -> None:
        self.client = client
        # Keyed by account hash, because one refresh asks about two of them: the status
        # questions pass "" and the Grand Exchange ones pass the character. A single slot
        # held only the most recent, so those two alternating keys evicted each other and
        # every call missed -- seven blocking round trips per tick instead of two.
        self._cached: dict[str, tuple[float, dict]] = {}
        self._last_good: dict[str, dict] = {}
        # Counted per key for the same reason the cache is: one tick asks twice, so a single
        # global counter reached two on one blip -- tolerating it for the status question and
        # then refusing it for the Grand Exchange one, in the same tick.
        self._consecutive_failures: dict[str, int] = {}

    @property
    def configured(self) -> bool:
        """Whether there is a desktop token to read with at all.

        Distinct from ``status_payload``'s ``detected`` ("is the plugin sending anything") —
        without this, an empty Settings dialog and a plugin gone quiet would look identical.
        """
        return self.client.configured

    @property
    def last_error(self) -> str | None:
        """What the most recent failed poll died of, for the "Website unreachable" status."""
        return self.client.last_error

    def _state(self, account_hash: str = "") -> dict:
        now = time.monotonic()
        cached = self._cached.get(account_hash)
        if cached is not None and now - cached[0] < STATE_CACHE_SECONDS:
            return cached[1]
        fetched = self.client.get("/api/sync-state", account_hash=account_hash)
        if isinstance(fetched, dict):
            self._consecutive_failures[account_hash] = 0
            self._last_good[account_hash] = fetched
            payload = fetched
        else:
            failures = self._consecutive_failures.get(account_hash, 0) + 1
            self._consecutive_failures[account_hash] = failures
            # One failed poll over a home internet connection is a dropped packet, not an
            # outage -- reusing the last good state for it is what stops "Website unreachable"
            # flapping on and off every few seconds. A second failure in a row still reports
            # it: this tolerates a blip, not a real interruption. Kept per character, so a
            # blip during a character switch never answers with the other one's slots.
            payload = self._last_good.get(account_hash, {}) if failures <= 1 else {}
        self._cached[account_hash] = (now, payload)
        return payload

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.client.configured:
            return (False, None, False)
        payload = self._state()
        if not payload:
            # Configured but unreachable — detected stays true so the app says "can't reach
            # the website", not "no plugin".
            return (True, None, False)
        status = payload.get("status")
        fresh = bool(payload.get("status_fresh"))
        return (True, status if isinstance(status, dict) else None, fresh)

    def known_accounts(self) -> list[dict]:
        characters = self._state().get("characters")
        return characters if isinstance(characters, list) else []

    def offer_state_payload(self, account_hash: str) -> dict | None:
        offers = self._state(account_hash).get("offers")
        return offers if isinstance(offers, dict) else None

    def offer_screen_payload(self, account_hash: str) -> dict | None:
        screen = self._state(account_hash).get("screen")
        return screen if isinstance(screen, dict) else None

    def pending(self, scan_limit: int) -> Iterator[PendingSyncEvent]:
        return iter(())

    def collected(self, handles: list[str]) -> None:
        return

    def quarantine(self, event: PendingSyncEvent) -> None:
        return

    def housekeeping(self) -> None:
        return
