"""Check a Cloudflare Turnstile token with Cloudflare.

Only account creation is gated on this. Signing in with Discord is already somebody else's
identity check, and signing back in with a recovery code needs a code that was issued to a
real person in the first place — the one door a script can walk through unaided is the one
that mints a brand new account, so that is the one with a challenge on it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS
from toolkit_web.token_probe import USER_AGENT

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify(secret: str, token: str) -> bool | None:
    """True if Cloudflare vouches for the token, False if it rejects it, ``None`` if it could
    not be asked at all.

    The third case is deliberately not folded into False. A failed lookup means Cloudflare was
    unreachable, which is worth "try again in a moment" rather than telling somebody who did
    nothing wrong that they failed a check they never saw.

    ``remoteip`` is left out on purpose: this runs behind a tunnel, so the only address it
    could send is either the loopback one it sees or whatever a forwarding header claims, and
    a spoofable value is worse than no value here.
    """
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
