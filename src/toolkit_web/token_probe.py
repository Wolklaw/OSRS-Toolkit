"""Ask the sync service a real yes-or-no about a pairing token.

``HttpSyncSource`` swallows every failure into the same empty result, on purpose — the plugin
and the importer only ever need to know "did anything come back", never why not. Saving a
token a person just pasted is different: a typo deserves "that token is wrong" on the spot, not
a page that quietly never syncs. So this makes its own request and reads the actual status
code rather than going through the source's forgiving path.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS

# The plugin's own name — Cloudflare turns away the standard library's default one outright.
USER_AGENT = "OSRS-Toolkit-Web/1.0 (+https://runescope.app)"


def mint_pairing_token(base_url: str, invite_code: str | None) -> str | None:
    """Ask the sync service for a fresh pairing token, or ``None`` on any failure.

    Nothing anywhere else in the system generates a token for a person to paste into the
    plugin — the plugin only ever accepts one, and the sync service's own ``/v1/pair`` is
    invite-gated while it is closed to the public. This is the one caller that is allowed to
    hold that invite code, so a signed-in visitor here never has to see or manage it
    themselves: the web app is trusted to mint on their behalf, the same as it already reads
    their journal on their behalf.
    """
    body = json.dumps({"invite_code": invite_code}).encode("utf-8")
    request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/pair", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None
    token = payload.get("token") if isinstance(payload, dict) else None
    return token if isinstance(token, str) and token else None


def probe_token(base_url: str, token: str) -> bool | None:
    """True if the token works, False if the service rejected it outright, ``None`` if
    nothing useful could be determined — the service could not be reached at all, which is a
    reason to say "try again in a moment", not "that token is wrong"."""
    request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/accounts", method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return False if error.code in (401, 403) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None
    return True
