"""Pull whatever the sync service is holding into a user's own journal.

There is no background worker. A read endpoint does this first and reads its own journal
second, so a page is never more than one request stale and there is no scheduler to keep
alive, restart, or explain when it silently stops. The cost is an HTTP round trip to the sync
service on every read — one call, and idempotent even if it happens twice — which is the right
trade for a service sized for a handful of users rather than one that needs to shave requests.
"""

from __future__ import annotations

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


def known_characters(settings: Settings, account: Account) -> list[dict]:
    """Every RuneLite character the sync service has seen play through this account's token,
    for a character switcher. Empty for an account with no pairing token — there is nothing to
    ask the sync service about."""
    if not account.pairing_token:
        return []
    source = HttpSyncSource(settings.sync_base_url, account.pairing_token)
    return source.known_accounts()


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
