from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS
from toolkit_web.token_probe import USER_AGENT

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify(secret: str, token: str) -> bool | None:
    if not token:
        return False
    body = urllib.parse.urlencode({"secret": secret, "response": token}).encode("utf-8")
    request = urllib.request.Request(VERIFY_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None
    return bool(payload.get("success")) if isinstance(payload, dict) else None
