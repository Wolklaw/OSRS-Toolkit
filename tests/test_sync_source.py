from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

from osrs_toolkit.runelite_sync import RuneLiteSyncImporter
from osrs_toolkit.sync_source import (
    USER_AGENT,
    HttpSyncSource,
    LocalFileSource,
    PendingSyncEvent,
)


def _response(payload: object) -> MagicMock:
    """A context-manager mock standing in for ``urlopen``'s return value."""
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _source() -> HttpSyncSource:
    return HttpSyncSource("https://sync.runescope.app", "test-token")


# -- request shape -----------------------------------------------------------------------


def test_every_call_carries_the_bearer_token_and_a_named_user_agent():
    with patch("urllib.request.urlopen", return_value=_response({"status": "ok"})) as urlopen:
        _source().status_payload()

    request = urlopen.call_args[0][0]
    assert request.get_header("Authorization") == "Bearer test-token"
    # Not politeness — Cloudflare turns away the standard library's own default name.
    assert request.get_header("User-agent") == USER_AGENT


def test_a_get_carries_no_body():
    with patch("urllib.request.urlopen", return_value=_response({"events": []})) as urlopen:
        list(_source().pending(scan_limit=500))

    request = urlopen.call_args[0][0]
    assert request.data is None
    assert request.get_method() == "GET"


def test_a_post_carries_a_json_body_and_content_type():
    with patch("urllib.request.urlopen", return_value=_response({"ok": True})) as urlopen:
        _source().collected(["abc", "def"])

    request = urlopen.call_args[0][0]
    assert json.loads(request.data) == {"event_ids": ["abc", "def"]}
    assert request.get_header("Content-type") == "application/json"
    assert request.get_method() == "POST"


# -- status_payload -----------------------------------------------------------------------


def test_status_payload_without_configuration_is_not_detected():
    assert HttpSyncSource("", "").status_payload() == (False, None, False)
    assert HttpSyncSource("https://sync.runescope.app", "").status_payload() == (
        False,
        None,
        False,
    )


def test_status_payload_reports_active_from_the_service():
    payload = {"active": True, "account_name": "Wolklaw"}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        detected, returned, active = _source().status_payload()

    assert (detected, active) == (True, True)
    assert returned["account_name"] == "Wolklaw"


def test_status_payload_when_the_service_cannot_be_reached_is_detected_but_not_active():
    """Configured but unreachable is a different problem from never installed, and the two
    must not collapse into the same answer — one is a network hiccup, the other means the
    player has nothing to fix in the plugin at all."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert _source().status_payload() == (True, None, False)


def test_status_payload_survives_a_malformed_response():
    with patch("urllib.request.urlopen", return_value=_response(["not", "a", "dict"])):
        assert _source().status_payload() == (True, None, False)


def test_status_payload_survives_invalid_json():
    response = MagicMock()
    response.read.return_value = b"{ this is not json"
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=response):
        assert _source().status_payload() == (True, None, False)


# -- offer state / screen -------------------------------------------------------------------


def test_offer_state_payload_reads_the_offers_key():
    with patch("urllib.request.urlopen", return_value=_response({"offers": {"3": {"itemId": 1}}})):
        assert _source().offer_state_payload("abc123") == {"3": {"itemId": 1}}


def test_offer_state_payload_is_none_when_the_service_has_nothing():
    with patch("urllib.request.urlopen", return_value=_response({"offers": None})):
        assert _source().offer_state_payload("abc123") is None


def test_offer_screen_payload_carries_its_own_updated_at_stamp():
    payload = {
        "screen": {"item_id": 21802, "side": "buy"},
        "screen_updated_at": "2026-08-22T00:00:00Z",
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        screen = _source().offer_screen_payload("abc123")

    assert screen == {
        "item_id": 21802,
        "side": "buy",
        "updated_at": "2026-08-22T00:00:00Z",
    }


def test_offer_screen_payload_is_none_when_no_screen_is_open():
    with patch("urllib.request.urlopen", return_value=_response({"screen": None})):
        assert _source().offer_screen_payload("abc123") is None


def test_account_hash_is_sanitised_before_it_reaches_the_url():
    with patch("urllib.request.urlopen", return_value=_response({"offers": None})) as urlopen:
        _source().offer_state_payload("abc123; DROP TABLE token--")

    request = urlopen.call_args[0][0]
    assert "abc123" in request.full_url
    assert "DROP" not in request.full_url
    assert ";" not in request.full_url


# -- pending / collected / quarantine --------------------------------------------------------


def test_pending_yields_events_with_their_id_as_the_handle():
    payload = {"events": [{"event_id": "e1", "event_type": "ge_fill"}]}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        events = list(_source().pending(scan_limit=500))

    assert events == [PendingSyncEvent("e1", payload["events"][0])]


def test_pending_skips_an_event_with_no_id_since_it_could_never_be_acknowledged():
    payload = {
        "events": [{"event_type": "ge_fill"}, {"event_id": "", "event_type": "ge_fill"}]
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        assert list(_source().pending(scan_limit=500)) == []


def test_pending_is_empty_when_the_service_is_unreachable():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert list(_source().pending(scan_limit=500)) == []


def test_collected_with_nothing_to_acknowledge_makes_no_request():
    with patch("urllib.request.urlopen") as urlopen:
        _source().collected([])

    urlopen.assert_not_called()


def test_quarantine_acknowledges_the_unusable_event():
    """Nowhere to file it and nothing gained by keeping it — the service would hand back the
    same unusable event on every later pass, so acknowledging is what drops it."""
    with patch("urllib.request.urlopen", return_value=_response({"deleted": 1})) as urlopen:
        _source().quarantine(PendingSyncEvent("bad-event", None))

    request = urlopen.call_args[0][0]
    assert json.loads(request.data) == {"event_ids": ["bad-event"]}


def test_http_source_housekeeping_makes_no_request():
    """The service prunes its own queue on its own schedule."""
    with patch("urllib.request.urlopen") as urlopen:
        _source().housekeeping()

    urlopen.assert_not_called()


# -- known_accounts ---------------------------------------------------------------------------


def test_known_accounts_returns_the_service_list():
    accounts = [{"account_hash": "abc123", "account_name": "Wolklaw", "last_seen": "..."}]
    with patch("urllib.request.urlopen", return_value=_response({"accounts": accounts})):
        assert _source().known_accounts() == accounts


def test_known_accounts_is_empty_when_the_service_cannot_be_reached():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert _source().known_accounts() == []


def test_known_accounts_survives_a_malformed_response():
    with patch("urllib.request.urlopen", return_value=_response({"accounts": "not-a-list"})):
        assert _source().known_accounts() == []


@dataclass
class _FakeSourceWithAccounts:
    def status_payload(self):
        return (True, None, False)

    def offer_state_payload(self, account_hash):
        return None

    def offer_screen_payload(self, account_hash):
        return None

    def pending(self, scan_limit):
        return iter(())

    def collected(self, handles):
        return None

    def quarantine(self, event):
        return None

    def housekeeping(self):
        return None

    def known_accounts(self):
        return [{"account_hash": "abc123", "account_name": None, "last_seen": None}]


def test_importer_known_accounts_delegates_to_a_source_that_supports_it():
    importer = RuneLiteSyncImporter(source=_FakeSourceWithAccounts())
    assert importer.known_accounts() == [
        {"account_hash": "abc123", "account_name": None, "last_seen": None}
    ]


def test_importer_known_accounts_is_empty_for_a_source_that_does_not_support_it():
    """The local file bridge never kept a registry of every character seen — only whichever
    one was logged in when it last wrote a file — so asking it must not raise."""
    importer = RuneLiteSyncImporter(source=LocalFileSource(Path("/nonexistent")))
    assert importer.known_accounts() == []
