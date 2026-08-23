"""Pull whatever the sync service is holding into a user's own journal.

There is no background worker. A read endpoint does this first and reads its own journal
second, so a page is never more than one request stale and there is no scheduler to keep
alive, restart, or explain when it silently stops. The cost is an HTTP round trip to the sync
service on every read — one call, and idempotent even if it happens twice — which is the right
trade for a service sized for a handful of users rather than one that needs to shave requests.
"""

from __future__ import annotations

import hashlib
import threading
import time

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import GEOfferScreen, GEOfferSlot, RuneLiteSyncImporter
from osrs_toolkit.sync_source import HttpSyncSource

from toolkit_web.accounts_db import Account
from toolkit_web.config import Settings


def journal_for(settings: Settings, account: Account) -> JournalRepository:
    """This account's own journal, importing anything the sync service is holding first.

    A no-op, safely, for an account with no pairing token yet — there is nothing to fetch, and
    the journal it already has is still worth reading.
    """
    repository = JournalRepository(settings.journal_path(account.discord_user_id))
    if account.pairing_token:
        source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
        RuneLiteSyncImporter(source=source).import_pending(repository)
    return repository


def stored_journal(settings: Settings, account: Account) -> JournalRepository:
    """This account's journal exactly as it is on disk, with no sync import first.

    ``journal_for`` calls the sync service before handing the journal back, which is right for
    a page render and wrong for anything on a timer: the desktop polls for a version number
    every minute, and making that reach out to another service -- on the same small machine
    hosting this one -- would turn the cheapest question here into the most expensive.
    """
    return JournalRepository(settings.journal_path(account.discord_user_id))


def known_characters(settings: Settings, account: Account) -> list[dict]:
    """Every RuneLite character the sync service has seen play through this account's token,
    for a character switcher. Empty for an account with no pairing token — there is nothing to
    ask the sync service about."""
    if not account.pairing_token:
        return []
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    return source.known_accounts()


#: How long one account's plugin status is trusted before it is asked for again. This is a
#: badge saying whether the game is running, so a few seconds of lag costs nothing -- while
#: the call behind it carries a 20-second timeout, which a dashboard render pays in full every
#: time the sync service is unreachable. Caching the unreachable answer as well as the good
#: ones is the point: an outage then costs one slow page per window rather than every page.
_ACTIVITY_TTL_SECONDS = 30

_activity_cache: dict[str, tuple[float, str]] = {}
_activity_lock = threading.Lock()


def _activity_key(token: str) -> str:
    """Hashed, so a long-lived process-wide dict is not where pairing tokens accumulate.

    Keying on the token rather than on the account is also what makes this need no explicit
    invalidation: pasting a different token is a different key and so a guaranteed miss, which
    matters because that is exactly when somebody is watching the badge to see if it worked.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def plugin_activity(settings: Settings, account: Account) -> str:
    """What the plugin is actually doing, as one of four words the UI can render.

    ``paired`` only ever meant "a token is saved", which is why a dashboard could say
    "Plugin connected" at a keyboard with the game shut. The sync service knows better --
    it reports whether anything is currently arriving -- so that is what gets asked here.

    - ``"none"`` — no token saved; nothing has been set up.
    - ``"live"`` — the service says the plugin is sending right now.
    - ``"idle"`` — set up and reachable, but nothing is arriving. RuneLite is closed, or the
      plugin is switched off. Not an error, and not "connected" either.
    - ``"unreachable"`` — the service could not be asked. Distinct from ``idle`` because the
      fix is different: nothing is lost, the plugin queues events until it comes back.
    """
    if not account.pairing_token:
        return "none"
    key = _activity_key(account.pairing_token)
    now = time.monotonic()
    with _activity_lock:
        cached = _activity_cache.get(key)
        if cached is not None and now - cached[0] < _ACTIVITY_TTL_SECONDS:
            return cached[1]
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    _detected, payload, active = source.status_payload()
    state = "unreachable" if payload is None else ("live" if active else "idle")
    with _activity_lock:
        # Expired entries for accounts nobody is looking at any more would otherwise sit here
        # forever; there is never more than one per account, but no reason to keep the dead.
        for stale, (stamp, _state) in list(_activity_cache.items()):
            if now - stamp >= _ACTIVITY_TTL_SECONDS:
                del _activity_cache[stale]
        _activity_cache[key] = (now, state)
    return state


def raw_sync_state(
    settings: Settings, account: Account, account_hash: str | None
) -> tuple[dict | None, bool, dict | None, dict | None]:
    """The sync service's own payloads, unparsed: (status, status_fresh, offers, screen).

    Deliberately not the parsed objects ``live_offer_state`` returns. This feeds the desktop
    window, which already owns tested parsers for exactly these shapes -- handing it the raw
    payload lets it keep using them, and keeps this app from becoming a second place where the
    meaning of a Grand Exchange slot is decided.
    """
    if not account.pairing_token:
        return None, False, None, None
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    _detected, status, fresh = source.status_payload()
    if not account_hash:
        return status, fresh, None, None
    return (
        status,
        fresh,
        source.offer_state_payload(account_hash),
        source.offer_screen_payload(account_hash),
    )


def live_offer_state(
    settings: Settings, account: Account, account_hash: str
) -> tuple[dict[int, GEOfferSlot], GEOfferScreen | None]:
    """This character's Grand Exchange slots right now, and what the "Set up offer" box is
    focused on if it's open — the same live state the desktop app's own GE dashboard reads,
    fetched fresh on every call since there is nothing here worth caching for longer than one
    page render."""
    if not account.pairing_token:
        return {}, None
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    importer = RuneLiteSyncImporter(source=source)
    return importer.read_offer_state(account_hash), importer.read_offer_screen(account_hash)
