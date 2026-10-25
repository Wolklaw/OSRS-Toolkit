from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import GEOfferScreen, GEOfferSlot, RuneLiteSyncImporter
from osrs_toolkit.sync_source import HttpSyncSource

from toolkit_web.accounts_db import Account
from toolkit_web.config import Settings

_IMPORT_INTERVAL_SECONDS = 4
_IMPORT_BACKOFF_SECONDS = 30
_SLOW_IMPORT_SECONDS = 3

_repositories: dict[Path, JournalRepository] = {}
_repositories_lock = threading.Lock()
_import_due: dict[str, float] = {}
_import_lock = threading.Lock()


def stored_journal(settings: Settings, account: Account) -> JournalRepository:
    path = settings.journal_path(account.discord_user_id)
    with _repositories_lock:
        repository = _repositories.get(path)
        if repository is None:
            repository = _repositories[path] = JournalRepository(path)
    return repository


def _claim_import(key: str) -> bool:
    now = time.monotonic()
    with _import_lock:
        if now < _import_due.get(key, 0.0):
            return False
        _import_due[key] = now + _IMPORT_BACKOFF_SECONDS
        return True


def _schedule_import(key: str, started: float) -> None:
    elapsed = time.monotonic() - started
    delay = _IMPORT_BACKOFF_SECONDS if elapsed > _SLOW_IMPORT_SECONDS else _IMPORT_INTERVAL_SECONDS
    with _import_lock:
        _import_due[key] = time.monotonic() + delay


def journal_for(settings: Settings, account: Account, *, force: bool = False) -> JournalRepository:
    repository = stored_journal(settings, account)
    if not account.pairing_token:
        return repository
    key = _activity_key(account.pairing_token)
    if not force and not _claim_import(key):
        return repository
    started = time.monotonic()
    try:
        source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
        RuneLiteSyncImporter(source=source).import_pending(repository)
    finally:
        _schedule_import(key, started)
    return repository


_CHARACTERS_TTL_SECONDS = 60

_characters_cache: dict[str, tuple[float, list[dict]]] = {}
_characters_lock = threading.Lock()


def known_characters(settings: Settings, account: Account) -> list[dict]:
    if not account.pairing_token:
        return []
    key = _activity_key(account.pairing_token)
    now = time.monotonic()
    with _characters_lock:
        cached = _characters_cache.get(key)
        if cached is not None and now - cached[0] < _CHARACTERS_TTL_SECONDS:
            return cached[1]
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    characters = source.known_accounts()
    if not characters:
        return characters
    with _characters_lock:
        for stale, (stamp, _list) in list(_characters_cache.items()):
            if now - stamp >= _CHARACTERS_TTL_SECONDS:
                del _characters_cache[stale]
        _characters_cache[key] = (now, characters)
    return characters


_ACTIVITY_TTL_SECONDS = 30

_activity_cache: dict[str, tuple[float, str]] = {}
_activity_lock = threading.Lock()


def _activity_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def plugin_activity(settings: Settings, account: Account) -> str:
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
        for stale, (stamp, _state) in list(_activity_cache.items()):
            if now - stamp >= _ACTIVITY_TTL_SECONDS:
                del _activity_cache[stale]
        _activity_cache[key] = (now, state)
    return state


def raw_sync_state(
    settings: Settings, account: Account, account_hash: str | None
) -> tuple[dict | None, bool, dict | None, dict | None]:
    if not account.pairing_token:
        return None, False, None, None
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    target = account_hash or ""
    status = source.state_payload(target)
    fresh = bool(status.get("active")) if status else False
    if not target:
        return status, fresh, None, None
    return (
        status,
        fresh,
        source.offer_state_payload(target),
        source.offer_screen_payload(target),
    )


def live_offer_state(
    settings: Settings, account: Account, account_hash: str
) -> tuple[dict[int, GEOfferSlot], GEOfferScreen | None]:
    if not account.pairing_token:
        return {}, None
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    importer = RuneLiteSyncImporter(source=source)
    return importer.read_offer_state(account_hash), importer.read_offer_screen(account_hash)
