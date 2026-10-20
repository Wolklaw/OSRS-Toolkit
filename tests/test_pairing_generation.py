from __future__ import annotations

import json
import re
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
    # previous_revoked says whether an older token was retired on the way. Nothing to retire
    # on a first pairing, so False here rather than absent — see the group at the bottom.
    assert response.json() == {
        "ok": True,
        "token": "fresh-token-abc",
        "previous_revoked": False,
    }
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


def test_a_paired_account_is_not_told_it_is_connected_until_something_arrives(
    signed_in: TestClient,
):
    """The bug this replaces: a saved token rendered as "Plugin connected" whether or not the
    game was running. Saving a token is not the same fact as data arriving."""
    with patch("toolkit_web.app.mint_pairing_token", return_value="fresh-token-abc"):
        signed_in.post("/api/pairing-token/generate")

    with patch("toolkit_web.app.plugin_activity", return_value="idle"):
        response = signed_in.get("/")

    assert badge_text(response.text) == "Idle"
    assert "Get a token" not in response.text


def test_a_paired_account_can_still_generate_a_replacement_token(signed_in: TestClient):
    with patch("toolkit_web.app.mint_pairing_token", return_value="first-token"):
        signed_in.post("/api/pairing-token/generate")

    with patch("toolkit_web.app.mint_pairing_token", return_value="second-token"):
        response = signed_in.post("/api/pairing-token/generate")

    assert response.json()["token"] == "second-token"


# -- one token at a time -------------------------------------------------------------------


def test_generating_a_token_retires_the_one_it_replaces(signed_in: TestClient):
    """The whole point. Every old token used to stay valid forever, so a plugin still holding
    one went on syncing into a pairing this app had stopped reading — silently, on both ends,
    and minting another to fix it only added a third."""
    with patch("toolkit_web.app.mint_pairing_token", return_value="first-token"):
        signed_in.post("/api/pairing-token/generate")

    with (
        patch("toolkit_web.app.mint_pairing_token", return_value="second-token"),
        patch("toolkit_web.app.revoke_pairing_token", return_value=True) as revoke,
        patch("toolkit_web.importing.RuneLiteSyncImporter"),
    ):
        body = signed_in.post("/api/pairing-token/generate").json()

    assert body["token"] == "second-token"
    assert body["previous_revoked"] is True
    revoke.assert_called_once()
    assert revoke.call_args[0][1] == "first-token"


def test_the_first_token_has_nothing_to_retire(signed_in: TestClient):
    with (
        patch("toolkit_web.app.mint_pairing_token", return_value="first-token"),
        patch("toolkit_web.app.revoke_pairing_token") as revoke,
    ):
        body = signed_in.post("/api/pairing-token/generate").json()

    assert body["previous_revoked"] is False
    revoke.assert_not_called()


def test_the_old_token_survives_a_mint_that_failed(signed_in: TestClient):
    """Revoking before the replacement exists would strand an account with no working token
    at all, for a service outage that changed nothing else."""
    with patch("toolkit_web.app.mint_pairing_token", return_value="first-token"):
        signed_in.post("/api/pairing-token/generate")

    with (
        patch("toolkit_web.app.mint_pairing_token", return_value=None),
        patch("toolkit_web.app.revoke_pairing_token") as revoke,
        patch("toolkit_web.importing.RuneLiteSyncImporter"),
    ):
        assert signed_in.post("/api/pairing-token/generate").status_code == 502

    revoke.assert_not_called()


def test_what_the_plugin_already_sent_is_drained_before_the_token_dies(signed_in: TestClient):
    """Events still queued under the old token become unreachable the moment it is revoked —
    no credential will ever name them again — so they are imported first."""
    with patch("toolkit_web.app.mint_pairing_token", return_value="first-token"):
        signed_in.post("/api/pairing-token/generate")

    with (
        patch("toolkit_web.app.mint_pairing_token", return_value="second-token"),
        patch("toolkit_web.app.revoke_pairing_token", return_value=True),
        patch("toolkit_web.importing.RuneLiteSyncImporter") as importer,
    ):
        signed_in.post("/api/pairing-token/generate")

    importer.return_value.import_pending.assert_called_once()


def badge_text(page: str) -> str:
    """What the plugin badge actually reads on screen.

    Checked against the element rather than by searching the whole page: the badge carries
    every state's wording in data- attributes so it can relabel itself without a reload, so
    "Sending now" appears in the markup of an idle page too. The rendered text is the thing
    these tests are really about.
    """
    match = re.search(
        r"<span class=\"badge[^\"]*\"[^>]*data-plugin-badge[^>]*>([^<]*)</span>", page
    )
    return match.group(1).strip() if match else ""


# -- a desktop token must not reach the plugin's credential ---------------------------------


def test_a_desktop_token_cannot_touch_the_pairing_token(signed_in: TestClient) -> None:
    """The same line drawn around account deletion and desktop credentials themselves.

    A pairing token is the plugin's *write* credential to the sync service, which is strictly
    more than the journal access a desktop token already carries -- so minting one, pointing
    the account at a different one, or disconnecting the plugin are all browser-only. Before
    this, a leaked desktop token could mint itself a pairing token, or silently repoint the
    account at an attacker's queue so fabricated trades landed in this journal.
    """
    token = signed_in.post("/api/desktop-tokens", json={"label": "Desktop"}).json()["token"]
    bare = TestClient(signed_in.app, base_url="https://testserver")
    headers = {"Authorization": f"Bearer {token}"}

    assert bare.post("/api/pairing-token/generate", headers=headers).status_code == 401
    assert bare.put("/api/pairing-token", json={"token": "x"}, headers=headers).status_code == 401
    assert bare.delete("/api/pairing-token", headers=headers).status_code == 401


def test_a_desktop_token_still_reaches_what_the_desktop_app_actually_uses(
    signed_in: TestClient,
) -> None:
    """The guard above must not have cost the desktop window the routes it does call."""
    token = signed_in.post("/api/desktop-tokens", json={"label": "Desktop"}).json()["token"]
    bare = TestClient(signed_in.app, base_url="https://testserver")
    headers = {"Authorization": f"Bearer {token}"}

    for path in ("/api/me", "/api/sync-state", "/api/journal/version"):
        assert bare.get(path, headers=headers).status_code == 200, path
