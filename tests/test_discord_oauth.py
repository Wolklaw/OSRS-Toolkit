from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

from toolkit_web.discord_oauth import authorize_url, exchange_code, fetch_user


def _response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_authorize_url_carries_the_essentials():
    url = authorize_url("client-123", "https://runescope.app/auth/discord/callback", "state-xyz")

    assert url.startswith("https://discord.com/api/oauth2/authorize?")
    assert "client_id=client-123" in url
    assert "state=state-xyz" in url
    assert "scope=identify" in url
    assert "runescope.app" in url


def test_exchange_code_returns_the_access_token():
    with patch("urllib.request.urlopen", return_value=_response(b'{"access_token": "tok-1"}')):
        assert exchange_code("id", "secret", "https://x/cb", "the-code") == "tok-1"


def test_exchange_code_returns_none_on_a_rejected_code():
    error = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert exchange_code("id", "secret", "https://x/cb", "reused-code") is None


def test_exchange_code_returns_none_when_discord_is_unreachable():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert exchange_code("id", "secret", "https://x/cb", "the-code") is None


def test_fetch_user_reads_id_username_and_builds_the_avatar_url():
    body = b'{"id": "111", "username": "Wolklaw", "avatar": "abcdef"}'
    with patch("urllib.request.urlopen", return_value=_response(body)):
        user = fetch_user("access-token")

    assert user.id == "111"
    assert user.username == "Wolklaw"
    assert user.avatar_url == "https://cdn.discordapp.com/avatars/111/abcdef.png"


def test_fetch_user_with_no_avatar_set_returns_none_for_it():
    body = b'{"id": "111", "username": "Wolklaw", "avatar": null}'
    with patch("urllib.request.urlopen", return_value=_response(body)):
        assert fetch_user("access-token").avatar_url is None


def test_fetch_user_returns_none_on_an_expired_token():
    error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert fetch_user("expired-token") is None


def test_fetch_user_survives_a_malformed_response():
    with patch("urllib.request.urlopen", return_value=_response(b'{"nothing": "useful"}')):
        assert fetch_user("access-token") is None
