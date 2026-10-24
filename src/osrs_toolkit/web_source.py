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

INTERACTIVE_TIMEOUT_SECONDS = 6.0

POLL_TIMEOUT_SECONDS = 2.5

STATE_CACHE_SECONDS = 2.0


def _describe_failure(error: Exception) -> str:
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
    pass


class ToolkitWebClient:
    def __init__(
        self, base_url: str | None = None, token: str = "", timeout: float | None = None
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or ""
        self.timeout = timeout or HTTP_TIMEOUT_SECONDS
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
        if not self.configured:
            raise ToolkitWebError("Enter the website address and a desktop access token.")
        payload = self.get("/api/me")
        if not isinstance(payload, dict) or not payload.get("username"):
            raise ToolkitWebError(
                "That token was not accepted. Generate a new one on the website's Profile page."
            )
        return str(payload["username"])


class WebAppSource:
    def __init__(self, client: ToolkitWebClient) -> None:
        self.client = client
        self._cached: dict[str, tuple[float, dict]] = {}
        self._last_good: dict[str, dict] = {}
        self._consecutive_failures: dict[str, int] = {}

    @property
    def configured(self) -> bool:
        return self.client.configured

    @property
    def last_error(self) -> str | None:
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
            payload = self._last_good.get(account_hash, {}) if failures <= 1 else {}
        self._cached[account_hash] = (now, payload)
        return payload

    def status_payload(self) -> tuple[bool, dict | None, bool]:
        if not self.client.configured:
            return (False, None, False)
        payload = self._state()
        if not payload:
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
