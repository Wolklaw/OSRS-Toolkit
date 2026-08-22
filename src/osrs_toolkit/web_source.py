"""Reading live plugin state from the website instead of from the plugin.

The RuneLite Plugin Hub will not accept a plugin that feeds an application installed on the
same machine, and ``LocalFileSource`` -- the plugin writing files into ``.runelite`` for this
app to pick up -- is exactly that arrangement. The plugin now posts to the sync service, the
website collects from it, and this app reads from the website. Nothing the plugin does reaches
this machine directly any more.

What arrives here is the sync service's own payloads, relayed unchanged by the website rather
than reshaped by it. That is deliberate: ``runelite_sync`` already owns tested parsers for
these shapes, and a relay keeps the website from becoming a second place where the meaning of
a Grand Exchange slot is decided.

Only the standard library, like every other module here -- the desktop app declares no runtime
dependencies at all, and a web client is not a good enough reason to start.
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

#: What the settings dialog's "Check connection" button waits, as opposed to the twenty seconds
#: a background refresh is happy to. Somebody has just pressed a button and is watching the
#: window: a wrong address has to come back as an answer while they are still looking at it,
#: not freeze the app long enough to look like a crash.
INTERACTIVE_TIMEOUT_SECONDS = 6.0

#: One dashboard refresh asks for status, then slots, then the open offer box. Those are three
#: questions about one moment, and over a home connection to a remote site three round trips
#: to answer them is three times the latency for no extra truth. A window this short collapses
#: them into one request without ever letting one refresh show another refresh's data.
STATE_CACHE_SECONDS = 2.0


class ToolkitWebError(Exception):
    """The website could be reached but refused, in a way worth telling the user about."""


class ToolkitWebClient:
    """Talks to one account's corner of the website, carrying a desktop credential.

    The credential is minted on the website's profile page and pasted in here. It is not the
    plugin's pairing token: that one is the plugin's write credential to the sync service, and
    a token that could also read somebody's whole journal would be a much worse thing to leak.
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

        Every failure answers the same way on purpose. A caller here is a dashboard refresh
        deciding whether it has something to draw, and "the site is down", "the token was
        revoked" and "the reply was not JSON" all mean the same thing to it: nothing to draw
        this time, try again on the next tick. ``ToolkitWebError`` is raised only where a
        caller has explicitly asked to be told -- see ``check``.
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

        Used by the settings dialog, which is the one place a person is owed a real answer
        rather than a quiet retry -- they have just typed something and want to know whether
        it was right.
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

    Live state -- who is logged in, what is in the eight Grand Exchange slots, what offer box
    is open -- is relayed from the sync service and parsed by the same code that has always
    parsed it.

    It yields no pending events, and that is not a gap. Events are the plugin's raw output, and
    the website has already imported them into the journal that this app now mirrors. Handing
    them over here as well would import each one twice, and acknowledging them would take them
    away from the website that has to serve everyone else's view of the same account.
    """

    def __init__(self, client: ToolkitWebClient) -> None:
        self.client = client
        self._cached: tuple[float, str, dict] | None = None

    def _state(self, account_hash: str = "") -> dict:
        now = time.monotonic()
        if self._cached is not None:
            cached_at, cached_hash, payload = self._cached
            if cached_hash == account_hash and now - cached_at < STATE_CACHE_SECONDS:
                return payload
        fetched = self.client.get("/api/sync-state", account_hash=account_hash)
        payload = fetched if isinstance(fetched, dict) else {}
        self._cached = (now, account_hash, payload)
        return payload

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.client.configured:
            return (False, None, False)
        payload = self._state()
        if not payload:
            # Configured but unreachable. Detected stays true so the app can say "cannot reach
            # the website" rather than "no plugin", which are different problems to fix.
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
