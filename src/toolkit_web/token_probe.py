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
