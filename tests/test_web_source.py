"""Reading live state from the website rather than from a folder the plugin wrote.

Mirrors ``test_sync_source``'s conventions -- ``urlopen`` is patched, nothing here touches a
network -- because this is the same kind of object doing the same job over a different wire.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from osrs_toolkit.sync_source import USER_AGENT
from osrs_toolkit.web_source import (
    ToolkitWebClient,
    ToolkitWebError,
    WebAppSource,
)


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _source() -> WebAppSource:
    return WebAppSource(ToolkitWebClient("https://runescope.app", "test-token"))


# -- request shape -----------------------------------------------------------------------


def test_every_call_carries_the_desktop_token_and_a_named_user_agent():
    with patch("urllib.request.urlopen", return_value=_response({"status": None})) as urlopen:
        _source().status_payload()

    request = urlopen.call_args[0][0]
    assert request.get_header("Authorization") == "Bearer test-token"
    assert request.get_header("User-agent") == USER_AGENT


def test_a_named_character_is_passed_as_a_query_parameter():
    with patch("urllib.request.urlopen", return_value=_response({"offers": {}})) as urlopen:
        _source().offer_state_payload("abc123")

    assert urlopen.call_args[0][0].full_url == (
        "https://runescope.app/api/sync-state?account_hash=abc123"
    )


def test_no_character_means_no_empty_query_parameter():
    with patch("urllib.request.urlopen", return_value=_response({"status": None})) as urlopen:
        _source().status_payload()

    assert urlopen.call_args[0][0].full_url == "https://runescope.app/api/sync-state"


def test_a_trailing_slash_on_the_address_does_not_double_up():
    client = ToolkitWebClient("https://runescope.app/", "test-token")
    with patch("urllib.request.urlopen", return_value=_response({})) as urlopen:
        client.get("/api/me")

    assert urlopen.call_args[0][0].full_url == "https://runescope.app/api/me"


# -- not configured ----------------------------------------------------------------------


def test_nothing_configured_reads_as_nothing_detected():
    assert WebAppSource(ToolkitWebClient("", "")).status_payload() == (False, None, False)
    assert WebAppSource(ToolkitWebClient("https://runescope.app", "")).status_payload() == (
        False,
        None,
        False,
    )


def test_an_unconfigured_client_never_reaches_the_network():
    with patch("urllib.request.urlopen") as urlopen:
        WebAppSource(ToolkitWebClient("https://runescope.app", "")).status_payload()
    urlopen.assert_not_called()


# -- status ------------------------------------------------------------------------------


def test_status_is_relayed_with_its_freshness():
    payload = {
        "status": {"account_name": "Wolklaw", "account_hash": "abc123", "active": True},
        "status_fresh": True,
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        detected, status, fresh = _source().status_payload()

    assert detected is True
    assert status == payload["status"]
    assert fresh is True


def test_configured_but_unreachable_stays_detected():
    """ "Cannot reach the website" and "no plugin at all" are different problems to fix, and
    the app says different things about them."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert _source().status_payload() == (True, None, False)


def test_a_reply_that_is_not_an_object_is_treated_as_unreachable():
    with patch("urllib.request.urlopen", return_value=_response(["not", "a", "dict"])):
        assert _source().status_payload() == (True, None, False)


def test_a_stale_status_is_reported_as_not_fresh():
    payload = {"status": {"account_name": "Wolklaw"}, "status_fresh": False}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        _detected, _status, fresh = _source().status_payload()
    assert fresh is False


# -- offers ------------------------------------------------------------------------------


def test_offers_and_screen_are_relayed_unparsed():
    """The website is a gate, not an interpreter -- what arrives is the sync service's own
    shape, so ``runelite_sync``'s existing parsers still apply to it."""
    payload = {
        "offers": {"3": {"itemId": 4151, "state": "BUYING"}},
        "screen": {"item_id": 4151, "item_name": "Abyssal whip"},
    }
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        source = _source()
        assert source.offer_state_payload("abc123") == payload["offers"]
        assert source.offer_screen_payload("abc123") == payload["screen"]


def test_missing_offers_read_as_nothing_rather_than_raising():
    with patch("urllib.request.urlopen", return_value=_response({"offers": None})):
        assert _source().offer_state_payload("abc123") is None
    with patch("urllib.request.urlopen", return_value=_response({"screen": None})):
        assert _source().offer_screen_payload("abc123") is None


def test_known_accounts_come_from_the_same_reply():
    payload = {"characters": [{"account_hash": "abc", "account_name": "Wolklaw"}]}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        assert _source().known_accounts() == payload["characters"]


# -- the one-request-per-refresh window ---------------------------------------------------


def test_one_refresh_asks_the_website_once():
    """Status, slots and the open offer box are three questions about one moment. Answering
    them in one round trip is the whole point of the cache."""
    payload = {"status": {}, "status_fresh": True, "offers": {}, "screen": None}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_screen_payload("abc123")

    assert urlopen.call_count == 1


def test_a_different_character_is_a_different_question():
    payload = {"offers": {}}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_state_payload("def456")

    assert urlopen.call_count == 2


def test_the_window_expires(monkeypatch):
    payload = {"offers": {}}
    # One reading per lookup: the first call stamps the cache, the second finds it long expired.
    clock = iter([0.0, 99.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_state_payload("abc123")

    assert urlopen.call_count == 2


# -- events are the website's, not ours ---------------------------------------------------


def test_no_events_are_ever_yielded():
    """The website has already imported these into the journal this app mirrors. Importing
    them again here would double every trade, and acknowledging them would take them away
    from the website."""
    with patch("urllib.request.urlopen") as urlopen:
        assert list(_source().pending(scan_limit=500)) == []
    urlopen.assert_not_called()


def test_acknowledging_and_housekeeping_reach_nothing():
    with patch("urllib.request.urlopen") as urlopen:
        source = _source()
        source.collected(["abc", "def"])
        source.housekeeping()
    urlopen.assert_not_called()


# -- checking a token --------------------------------------------------------------------


def test_check_returns_the_account_name():
    with patch("urllib.request.urlopen", return_value=_response({"username": "Wolklaw"})):
        assert _source().client.check() == "Wolklaw"


def test_check_complains_about_an_empty_configuration():
    try:
        ToolkitWebClient("https://runescope.app", "").check()
    except ToolkitWebError as error:
        assert "desktop access token" in str(error)
    else:
        raise AssertionError("an unconfigured client should refuse to pretend it works")


def test_check_complains_when_the_token_is_refused():
    with patch(
        "urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)
    ):
        try:
            ToolkitWebClient("https://runescope.app", "stale").check()
        except ToolkitWebError as error:
            assert "Profile page" in str(error)
        else:
            raise AssertionError("a refused token should be reported, not swallowed")
