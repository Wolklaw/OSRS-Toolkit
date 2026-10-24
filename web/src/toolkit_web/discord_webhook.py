from __future__ import annotations

import json
import urllib.error
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS

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
