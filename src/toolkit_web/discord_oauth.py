"""Sign-in with Discord: the OAuth2 authorization code flow, and nothing more of it.

Only the ``identify`` scope is asked for — a user id, a username, an avatar hash. Nothing here
ever sees a Discord password, and nothing here asks for a scope this app has no use for. The
access token returned by the exchange is used once, to read the profile, and then discarded:
this app has no reason to act as the user on Discord again later, so there is nothing to store
and nothing to refresh.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
USER_URL = "https://discord.com/api/users/@me"
TIMEOUT_SECONDS = 15

# Discord's API sits behind Cloudflare, which turns away the standard library's default
# User-Agent outright — the same "error code: 1010" block the sync service applies to its own
# callers. Every request here needs a real one or Discord never sees it at all.
USER_AGENT = "OSRS-Toolkit-Web/1.0 (+https://runescope.app)"


@dataclass(frozen=True, slots=True)
class DiscordUser:
    id: str
    username: str
    avatar_url: str | None


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify",
            "state": state,
            # Discord otherwise silently re-approves a user who already authorized once,
            # which is fine — asking every time would just be friction with no benefit here.
            "prompt": "none",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> str | None:
    """The access token for a freshly granted authorization, or ``None`` on any failure.

    Every failure — a wrong or reused code, an outage on Discord's side, a network hiccup —
    ends in the same place: no session is issued. There is nothing a caller can usefully do
    differently for one failure over another, since the fix in every case is the same: send
    the user to sign in again.
    """
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        # Discord's error body says exactly what was wrong with the request — a bad secret, a
        # redirect_uri that does not match what is registered, a reused code — and that reason
        # is worth a line in the log even though the caller only ever sees "sign in again".
        log.warning("Discord token exchange failed (%s): %s", error.code, error.read())
        return None
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
        OSError,
    ) as error:
        log.warning("Discord token exchange failed: %r", error)
        return None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    return token if isinstance(token, str) and token else None


def fetch_user(access_token: str) -> DiscordUser | None:
    request = urllib.request.Request(USER_URL, method="GET")
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        log.warning("Discord profile fetch failed (%s): %s", error.code, error.read())
        return None
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
        OSError,
    ) as error:
        log.warning("Discord profile fetch failed: %r", error)
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("id")
    username = payload.get("username")
    if not isinstance(user_id, str) or not user_id or not isinstance(username, str):
        return None
    avatar_hash = payload.get("avatar")
    avatar = (
        f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
        if isinstance(avatar_hash, str) and avatar_hash
        else None
    )
    return DiscordUser(id=user_id, username=username, avatar_url=avatar)
