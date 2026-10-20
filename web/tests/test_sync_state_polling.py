"""How many times drawing the live-state panel asks the sync service.

The dashboard polls ``/api/sync-state`` every few seconds, and it used to cost four requests
to the sync service per poll: the character list, then the same ``/v1/state`` response fetched
three separate times for three different keys out of it. On a home server that is the most
expensive question this app asks, asked constantly, and the three copies of one answer could
each describe a different moment.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from toolkit_web import importing


@pytest.fixture(autouse=True)
def _clear_caches():
    """Both caches are keyed by pairing token and live for the process, so tests reusing a
    token would otherwise read each other's answers and pass in the wrong order."""
    importing._activity_cache.clear()
    importing._characters_cache.clear()
    yield
    importing._activity_cache.clear()
    importing._characters_cache.clear()


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _paired(client: TestClient) -> TestClient:
    with patch("toolkit_web.app.probe_token", return_value=True):
        assert client.put("/api/pairing-token", json={"token": "good-token"}).status_code == 200
    return client


def test_one_poll_costs_one_request_to_the_sync_service(signed_in: TestClient):
    _paired(signed_in)
    state = {
        "active": True,
        "account_hash": "abc123",
        "account_name": "Lord Wolklaw",
        "offers": {"3": {"itemId": 1}},
        "screen": None,
    }
    characters = [{"account_hash": "abc123", "account_name": "Lord Wolklaw"}]

    with (
        patch("toolkit_web.importing.HttpSyncSource.known_accounts", return_value=characters),
        patch("urllib.request.urlopen", return_value=_response(state)) as urlopen,
    ):
        body = signed_in.get("/api/sync-state").json()

    assert body["account_hash"] == "abc123"
    assert body["offers"] == {"3": {"itemId": 1}}
    assert body["status_fresh"] is True
    assert urlopen.call_count == 1


def test_the_character_list_is_not_re_fetched_on_every_poll(signed_in: TestClient):
    _paired(signed_in)
    characters = [{"account_hash": "abc123", "account_name": "Lord Wolklaw"}]
    state = {"active": True, "account_hash": "abc123", "offers": None, "screen": None}

    with (
        patch(
            "toolkit_web.importing.HttpSyncSource.known_accounts", return_value=characters
        ) as known,
        patch("urllib.request.urlopen", return_value=_response(state)),
    ):
        for _ in range(5):
            signed_in.get("/api/sync-state")

    assert known.call_count == 1


def test_a_pairing_with_no_characters_yet_is_asked_again_next_poll(signed_in: TestClient):
    """The seconds between pasting a token and the plugin's first heartbeat look exactly like
    an unreachable service. Remembering either for a minute would hide a character from the
    person sitting there watching for it to appear."""
    _paired(signed_in)
    state = {"active": False, "account_hash": None, "offers": None, "screen": None}

    with (
        patch("toolkit_web.importing.HttpSyncSource.known_accounts", return_value=[]) as known,
        patch("urllib.request.urlopen", return_value=_response(state)),
    ):
        signed_in.get("/api/sync-state")
        signed_in.get("/api/sync-state")

    assert known.call_count == 2
