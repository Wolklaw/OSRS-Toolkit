"""Pull whatever the sync service is holding into a user's own journal.

There is no background worker. A read endpoint does this first and reads its own journal
second, so a page is never more than one request stale and there is no scheduler to keep
alive, restart, or explain when it silently stops. The cost is an HTTP round trip to the sync
service on every read — one call, and idempotent even if it happens twice — which is the right
trade for a service sized for a handful of users rather than one that needs to shave requests.
"""

from __future__ import annotations

from osrs_toolkit.journal import JournalRepository
from osrs_toolkit.runelite_sync import RuneLiteSyncImporter
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
