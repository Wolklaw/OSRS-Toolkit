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

# Collapses one refresh's status/slots/offer-box calls into a single request instead of
# three round trips.
STATE_CACHE_SECONDS = 2.0


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

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def get(self, path: str, **params: str) -> object | None:
        return self._call("GET", path, params=params)

    def post(self, path: str, body: object) -> object | None:
        return self._call("POST", path, body=body)

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> object | None:
        """The response as JSON, or ``None`` for anything that did not clearly succeed.

        Every failure answers the same way on purpose — a dashboard refresh treats "site
        down", "token revoked", and "reply wasn't JSON" identically: nothing to draw, try next
        tick. ``ToolkitWebError`` is raised only where a caller asks to be told — see ``check``.
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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"null")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
            return None

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
        self._cached: tuple[float, str, dict] | None = None
        self._last_good: tuple[str, dict] | None = None
        self._consecutive_failures = 0

    @property
    def configured(self) -> bool:
        """Whether there is a desktop token to read with at all.

        Distinct from ``status_payload``'s ``detected`` ("is the plugin sending anything") —
        without this, an empty Settings dialog and a plugin gone quiet would look identical.
        """
        return self.client.configured

    def _state(self, account_hash: str = "") -> dict:
        now = time.monotonic()
        if self._cached is not None:
            cached_at, cached_hash, payload = self._cached
            if cached_hash == account_hash and now - cached_at < STATE_CACHE_SECONDS:
                return payload
        fetched = self.client.get("/api/sync-state", account_hash=account_hash)
        if isinstance(fetched, dict):
            self._consecutive_failures = 0
            self._last_good = (account_hash, fetched)
            payload = fetched
        else:
            self._consecutive_failures += 1
            # One failed poll over a home internet connection is a dropped packet, not an
            # outage -- reusing the last good state for it is what stops "Website unreachable"
            # flapping on and off every few seconds. A second failure in a row still reports
            # it: this tolerates a blip, not a real interruption.
            if self._consecutive_failures <= 1 and self._last_good is not None:
                last_hash, last_payload = self._last_good
                payload = last_payload if last_hash == account_hash else {}
            else:
                payload = {}
        self._cached = (now, account_hash, payload)
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
