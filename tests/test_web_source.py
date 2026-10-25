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
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert _source().status_payload() == (True, None, False)


def test_a_reply_that_is_not_an_object_is_treated_as_unreachable():
    with patch("urllib.request.urlopen", return_value=_response(["not", "a", "dict"])):
        assert _source().status_payload() == (True, None, False)


def test_the_client_remembers_why_the_last_call_failed():
    client = ToolkitWebClient("https://runescope.app", "test-token")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        client.get("/api/me")
    assert client.last_error == "offline"


def test_an_http_error_is_described_by_its_status_code():
    client = ToolkitWebClient("https://runescope.app", "test-token")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None),
    ):
        client.get("/api/me")
    assert client.last_error == "HTTP 503"


def test_a_timeout_is_described_as_a_timeout():
    client = ToolkitWebClient("https://runescope.app", "test-token")
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        client.get("/api/me")
    assert client.last_error == "timed out"


def test_a_success_clears_the_remembered_error():
    client = ToolkitWebClient("https://runescope.app", "test-token")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        client.get("/api/me")
    assert client.last_error is not None

    with patch("urllib.request.urlopen", return_value=_response({"username": "Wolklaw"})):
        client.get("/api/me")
    assert client.last_error is None


def test_the_source_exposes_the_clients_last_error():
    source = _source()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        source.status_payload()
    assert source.last_error == "offline"


def test_a_stale_status_is_reported_as_not_fresh():
    payload = {"status": {"account_name": "Wolklaw"}, "status_fresh": False}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        _detected, _status, fresh = _source().status_payload()
    assert fresh is False


def test_one_dropped_poll_reuses_the_last_good_state(monkeypatch):
    payload = {"status": {"account_name": "Wolklaw", "active": True}, "status_fresh": True}
    clock = iter([0.0, 10.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    source = _source()
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        first = source.status_payload()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("blip")):
        second = source.status_payload()

    assert first == (True, payload["status"], True)
    assert second == first


def test_one_blip_is_absorbed_by_the_status_and_the_slots_alike(monkeypatch):
    payload = {
        "status": {"account_name": "Wolklaw", "active": True},
        "status_fresh": True,
        "offers": {"3": {"itemId": 4151}},
    }
    clock = iter([0.0, 0.0, 10.0, 10.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    source = _source()
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        source.status_payload()
        source.offer_state_payload("abc123")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("blip")):
        _detected, status, _fresh = source.status_payload()
        offers = source.offer_state_payload("abc123")

    assert status == payload["status"]
    assert offers == payload["offers"]


def test_two_dropped_polls_in_a_row_do_report_unreachable(monkeypatch):
    payload = {"status": {"account_name": "Wolklaw"}, "status_fresh": True}
    clock = iter([0.0, 10.0, 20.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    source = _source()
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        source.status_payload()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("blip")):
        source.status_payload()
        assert source.status_payload() == (True, None, False)


def test_a_dropped_poll_is_not_reused_for_a_different_character(monkeypatch):
    payload = {"offers": {"3": {"itemId": 4151}}}
    clock = iter([0.0, 10.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    source = _source()
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        source.offer_state_payload("abc123")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("blip")):
        assert source.offer_state_payload("def456") is None


def test_offers_and_screen_are_relayed_unparsed():
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


def test_one_refresh_asks_the_website_once():
    payload = {"status": {}, "status_fresh": True, "offers": {}, "screen": None}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_screen_payload("abc123")

    assert urlopen.call_count == 1


def test_alternating_between_the_status_and_a_character_still_costs_two_requests():
    payload = {"status": {}, "status_fresh": True, "offers": {}, "screen": None}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        for _ in range(4):
            source.status_payload()
            source.offer_state_payload("abc123")

    assert urlopen.call_count == 2


def test_a_different_character_is_a_different_question():
    payload = {"offers": {}}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_state_payload("def456")

    assert urlopen.call_count == 2


def test_the_window_expires(monkeypatch):
    payload = {"offers": {}}
    clock = iter([0.0, 99.0])
    monkeypatch.setattr("osrs_toolkit.web_source.time.monotonic", lambda: next(clock))
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        source = _source()
        source.offer_state_payload("abc123")
        source.offer_state_payload("abc123")

    assert urlopen.call_count == 2


def test_no_events_are_ever_yielded():
    with patch("urllib.request.urlopen") as urlopen:
        assert list(_source().pending(scan_limit=500)) == []
    urlopen.assert_not_called()


def test_acknowledging_and_housekeeping_reach_nothing():
    with patch("urllib.request.urlopen") as urlopen:
        source = _source()
        source.collected(["abc", "def"])
        source.housekeeping()
    urlopen.assert_not_called()


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


def test_a_background_source_never_calls_the_network_from_the_caller():
    import threading
    import time

    source = _source()
    callers: list[str] = []
    answered = threading.Event()

    def fetch(path: str, **_params: str) -> dict:
        callers.append(threading.current_thread().name)
        answered.set()
        return {"offers": {"0": {"item_id": 4151}}}

    source.client.get = fetch
    source.start_background(interval=0.05)
    try:
        assert source.offer_state_payload("abc") in (None, {"0": {"item_id": 4151}})
        assert answered.wait(2)
        deadline = time.monotonic() + 2
        while source.offer_state_payload("abc") is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert source.offer_state_payload("abc") == {"0": {"item_id": 4151}}
    finally:
        source.stop_background()

    assert set(callers) == {"sync-state-poller"}
