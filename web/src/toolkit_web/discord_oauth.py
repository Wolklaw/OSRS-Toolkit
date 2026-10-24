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
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> str | None:
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
