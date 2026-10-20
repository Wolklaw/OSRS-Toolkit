"""Post a price alert to a Discord webhook the account owner configured for themselves.

Validated to actually be a Discord webhook URL before it is ever saved — this app is shared by
many accounts, and without that check saving a "webhook URL" would really be saving an arbitrary
URL this server later POSTs to on a schedule, on someone else's say-so.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS

# The plugin's own name — Cloudflare turns away the standard library's default one outright.
USER_AGENT = "OSRS-Toolkit-Web/1.0 (+https://runescope.app)"

_ALLOWED_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)


def is_discord_webhook_url(url: str) -> bool:
    return url.startswith(_ALLOWED_PREFIXES)


def send_webhook_message(webhook_url: str, content: str) -> bool:
    """Post one message. True on success; failures are swallowed the same way every other
    best-effort outbound call in this app is — a missed alert costs a re-check next run, not
    a broken page."""
    body = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(webhook_url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True
