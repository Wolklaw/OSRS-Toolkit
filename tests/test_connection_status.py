"""What the Dashboard says is connected, and whether that is true.

The bug these cover: ``Account.paired`` only ever meant "a pairing token is saved", and the
Dashboard rendered it as "Plugin connected" — so the page claimed a live plugin at a keyboard
with the game shut. Configured and arriving are two different facts and both are worth saying.
"""

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
    """The status cache is keyed by pairing token and lives for the process, so tests that
    reuse a token would otherwise read each other's answers and pass in the wrong order."""
    importing._activity_cache.clear()
    yield
    importing._activity_cache.clear()


class _Token:
    """Just enough of a DesktopToken for the activity read, which only looks at one field."""

    def __init__(self, last_used_at: str | None) -> None:
        self.last_used_at = last_used_at


def _ago(**delta) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat().replace("+00:00", "Z")


# -- the plugin ---------------------------------------------------------------------------


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
    """RuneLite closed, or the master switch off. Set up, but not sending."""

    class Paired:
        pairing_token = "token"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload",
        return_value=(True, {"active": False}, False),
    ):
        assert plugin_activity(settings, Paired()) == "idle"


def test_an_unreachable_service_is_not_reported_as_idle(settings):
    """Different problem, different fix — and nothing is lost, so it must not read as "off"."""

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
    """A dashboard render used to pay the sync call every time, and that call carries a
    20-second timeout — so an unreachable service cost every page load, not just the first."""

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
    """Otherwise the outage case — the expensive one — is the one that caches nothing."""

    class Paired:
        pairing_token = "cache-the-outage"

    with patch(
        "toolkit_web.importing.HttpSyncSource.status_payload", return_value=(True, None, False)
    ) as status:
        assert importing.plugin_activity(settings, Paired()) == "unreachable"
        assert importing.plugin_activity(settings, Paired()) == "unreachable"

    status.assert_called_once()


def test_a_different_token_is_never_served_another_tokens_verdict(settings):
    """Keyed on the token, so pasting a new one is a guaranteed miss — which is the moment
    somebody is staring at the badge to see whether what they pasted worked."""

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


# -- the desktop app ----------------------------------------------------------------------


def test_no_desktop_token_is_nothing_set_up():
    assert _desktop_activity([]) == ("none", None)


def test_a_token_that_has_never_been_used_is_waiting_rather_than_connected():
    """Almost always somebody who copied a token and has not pasted it in yet."""
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
    """Two PCs, one of them on right now — the account is connected, not idle."""
    state, _last = _desktop_activity([_Token(_ago(days=2)), _Token(_ago(seconds=10))])

    assert state == "live"


def test_an_unparseable_stamp_does_not_break_the_page():
    assert _desktop_activity([_Token("not a timestamp")]) == ("waiting", None)


def test_the_dashboard_shows_the_desktop_app_even_with_nothing_connected(signed_in: TestClient):
    """The complaint that started this: the page said nothing at all about the desktop app."""
    page = signed_in.get("/").text

    assert "Windows app" in page
    assert "/download" in page


def test_the_dashboard_reports_a_connected_desktop_app(signed_in: TestClient):
    signed_in.post("/api/desktop-tokens", json={"label": "This PC"})
    # Minting alone is not using it; the poll that follows is what marks the app as running.
    assert "Token unused" in signed_in.get("/").text

    token = signed_in.post("/api/desktop-tokens", json={"label": "Second PC"}).json()["token"]
    # Polled from a client with no session cookie, the way the real desktop app has none. The
    # cookie takes precedence over the bearer header where both are sent, so a client carrying
    # both would resolve the account without ever touching the token's last-used stamp.
    desktop = TestClient(signed_in.app, base_url="https://testserver")
    desktop.get("/api/journal/version", headers={"Authorization": f"Bearer {token}"})

    # Asserted on the row's own sentence rather than on the badge word: "Connected" also
    # appears in a script on this page, so matching it would pass with the row still wrong.
    assert "Mirroring this journal" in signed_in.get("/").text


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
