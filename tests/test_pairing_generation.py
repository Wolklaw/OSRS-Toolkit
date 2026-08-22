from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from toolkit_web.token_probe import mint_pairing_token


def _response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


# -- mint_pairing_token itself --------------------------------------------------------------


def test_mint_returns_the_token_and_carries_the_invite_code():
    with patch("urllib.request.urlopen", return_value=_response(b'{"token": "abc123"}')) as urlopen:
        token = mint_pairing_token("https://sync.example.invalid", "invite-code")

    assert token == "abc123"
    request = urlopen.call_args[0][0]
    assert json.loads(request.data) == {"invite_code": "invite-code"}


def test_mint_returns_none_when_the_service_refuses():
    error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert mint_pairing_token("https://sync.example.invalid", "wrong-code") is None


def test_mint_returns_none_when_unreachable():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert mint_pairing_token("https://sync.example.invalid", "invite-code") is None


# -- the route -------------------------------------------------------------------------------


def test_generating_a_token_pairs_the_account(signed_in: TestClient):
    with patch("toolkit_web.app.mint_pairing_token", return_value="fresh-token-abc"):
        response = signed_in.post("/api/pairing-token/generate")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "token": "fresh-token-abc"}
    assert signed_in.get("/api/me").json()["paired"] is True


def test_generating_a_token_does_not_go_through_the_probe(signed_in: TestClient):
    """A token this app just minted from the sync service is trusted outright — asking the
    service to confirm its own answer would be redundant, and the wrong signal if that
    second call happened to fail."""
    with (
        patch("toolkit_web.app.mint_pairing_token", return_value="fresh-token-abc"),
        patch("toolkit_web.app.probe_token") as probe,
    ):
        signed_in.post("/api/pairing-token/generate")

    probe.assert_not_called()


def test_generation_failure_does_not_pair_the_account(signed_in: TestClient):
    with patch("toolkit_web.app.mint_pairing_token", return_value=None):
        response = signed_in.post("/api/pairing-token/generate")

    assert response.status_code == 502
    assert signed_in.get("/api/me").json()["paired"] is False


def test_generating_a_token_needs_a_session(client: TestClient):
    assert client.post("/api/pairing-token/generate").status_code == 401


def test_the_dashboard_offers_generation_when_unpaired(signed_in: TestClient):
    response = signed_in.get("/")

    assert "Get a token" in response.text
