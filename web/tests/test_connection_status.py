from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from toolkit_web import importing
from toolkit_web.app import _desktop_activity
from toolkit_web.importing import plugin_activity


@pytest.fixture(autouse=True)
def _clear_activity_cache():
    importing._activity_cache.clear()
    yield
    importing._activity_cache.clear()


class _Token:
    def __init__(self, last_used_at: str | None) -> None:
        self.last_used_at = last_used_at


def _ago(**delta) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat().replace("+00:00", "Z")


def test_no_token_is_reported_as_nothing_set_up(settings):
    class Unpaired:
        pairing_token = None

    assert plugin_activity(settings, Unpaired()) == "none"


def test_a_token_with_the_service_reporting_active_is_live(settings):
    class Paired:
        pairing_token = "token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": True}, True),
    ):
        assert plugin_activity(settings, Paired()) == "live"


def test_a_token_with_the_service_reporting_nothing_arriving_is_idle(settings):
    class Paired:
        pairing_token = "token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": False}, False),
    ):
        assert plugin_activity(settings, Paired()) == "idle"


def test_an_unreachable_service_is_not_reported_as_idle(settings):
    class Paired:
        pairing_token = "token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload", return_value=(True, None, False)
    ):
        assert plugin_activity(settings, Paired()) == "unreachable"


def test_the_dashboard_says_sending_only_when_the_plugin_is_sending(signed_in: TestClient):
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    with patch("toolkit_web.app.plugin_activity", return_value="live"):
        assert badge_text(signed_in.get("/").text) == "Sending now"

    with patch("toolkit_web.app.plugin_activity", return_value="idle"):
        page = signed_in.get("/").text
    assert badge_text(page) != "Sending now"
    assert "Idle" in page


def test_an_unreachable_sync_service_says_so_rather_than_blaming_the_plugin(
    signed_in: TestClient,
):
    with patch("toolkit_web.app.probe_token", return_value=True):
        signed_in.put("/api/pairing-token", json={"token": "good-token"})

    with patch("toolkit_web.app.plugin_activity", return_value="unreachable"):
        page = signed_in.get("/").text

    assert "No answer" in page
    assert "Nothing is lost" in page


def test_the_status_is_not_re_fetched_on_every_render(settings):
    class Paired:
        pairing_token = "cache-me"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": True}, True),
    ) as status:
        assert importing.plugin_activity(settings, Paired()) == "live"
        assert importing.plugin_activity(settings, Paired()) == "live"

    status.assert_called_once()


def test_an_unreachable_answer_is_cached_too(settings):
    class Paired:
        pairing_token = "cache-the-outage"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload", return_value=(True, None, False)
    ) as status:
        assert importing.plugin_activity(settings, Paired()) == "unreachable"
        assert importing.plugin_activity(settings, Paired()) == "unreachable"

    status.assert_called_once()


def test_a_different_token_is_never_served_another_tokens_verdict(settings):
    class First:
        pairing_token = "first-token"

    class Second:
        pairing_token = "second-token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": True}, True),
    ):
        assert importing.plugin_activity(settings, First()) == "live"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": False}, False),
    ):
        assert importing.plugin_activity(settings, Second()) == "idle"


def test_the_cache_does_not_hold_pairing_tokens_in_the_clear(settings):
    class Paired:
        pairing_token = "secret-pairing-token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": True}, True),
    ):
        importing.plugin_activity(settings, Paired())

    assert "secret-pairing-token" not in importing._activity_cache


def test_no_desktop_token_is_nothing_set_up():
    assert _desktop_activity([]) == ("none", None)


def test_a_token_that_has_never_been_used_is_waiting_rather_than_connected():
    state, last = _desktop_activity([_Token(None)])

    assert state == "waiting"
    assert last is None


def test_a_recently_used_token_is_a_connected_app():
    state, _last = _desktop_activity([_Token(_ago(seconds=30))])

    assert state == "live"


def test_a_token_last_used_hours_ago_is_an_app_that_is_not_running():
    state, last = _desktop_activity([_Token(_ago(hours=3))])

    assert state == "idle"
    assert last is not None


def test_the_most_recent_token_decides_when_several_exist():
    state, _last = _desktop_activity([_Token(_ago(days=2)), _Token(_ago(seconds=10))])

    assert state == "live"


def test_an_unparseable_stamp_does_not_break_the_page():
    assert _desktop_activity([_Token("not a timestamp")]) == ("waiting", None)


def test_the_dashboard_shows_the_desktop_app_even_with_nothing_connected(signed_in: TestClient):
    page = signed_in.get("/").text

    assert "Windows app" in page
    assert "/download" in page


def test_the_dashboard_reports_a_connected_desktop_app(signed_in: TestClient):
    signed_in.post("/api/desktop-tokens", json={"label": "This PC"})
    assert "Token unused" in signed_in.get("/").text

    token = signed_in.post("/api/desktop-tokens", json={"label": "Second PC"}).json()["token"]
    desktop = TestClient(signed_in.app, base_url="https://testserver")
    desktop.get("/api/journal/version", headers={"Authorization": f"Bearer {token}"})

    assert "Mirroring this journal" in signed_in.get("/").text


def badge_text(page: str) -> str:
    match = re.search(
        r"<span class=\"badge[^\"]*\"[^>]*data-plugin-badge[^>]*>([^<]*)</span>", page
    )
    return match.group(1).strip() if match else ""
