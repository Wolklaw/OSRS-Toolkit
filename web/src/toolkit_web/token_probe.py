from __future__ import annotations

import json
import urllib.error
import urllib.request

from toolkit_web.discord_oauth import TIMEOUT_SECONDS

USER_AGENT = "OSRS-Toolkit-Web/1.0 (+https://runescope.app)"


def mint_pairing_token(base_url: str, invite_code: str | None) -> str | None:
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


def revoke_pairing_token(base_url: str, token: str) -> bool:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/pair", method="DELETE")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as error:
        return error.code in (401, 403)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True


def probe_token(base_url: str, token: str) -> bool | None:
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
