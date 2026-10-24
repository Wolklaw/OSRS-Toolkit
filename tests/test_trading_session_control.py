from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from osrs_toolkit.app import MainWindow
from osrs_toolkit.web_source import ToolkitWebClient


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _client() -> ToolkitWebClient:
    return ToolkitWebClient("https://runescope.app", "test-token")


def test_reading_the_session_scopes_it_to_the_character():
    with patch("urllib.request.urlopen", return_value=_response({"active": None})) as urlopen:
        _client().trading_session("abc123")

    assert urlopen.call_args[0][0].full_url == (
        "https://runescope.app/api/trading-session?account_hash=abc123"
    )


def test_no_character_means_the_session_covers_all_of_them():
    with patch("urllib.request.urlopen", return_value=_response({"active": None})) as urlopen:
        _client().trading_session("")

    assert urlopen.call_args[0][0].full_url == "https://runescope.app/api/trading-session"


def test_starting_a_session_posts_the_backdate():
    with patch("urllib.request.urlopen", return_value=_response({"active": {}})) as urlopen:
        _client().start_trading_session("abc123", 60)

    request = urlopen.call_args[0][0]
    assert request.method == "POST"
    assert json.loads(request.data) == {"backdate_minutes": 60}


def test_stopping_a_session_uses_delete():
    with patch("urllib.request.urlopen", return_value=_response({"ended": {}})) as urlopen:
        _client().stop_trading_session("abc123")

    assert urlopen.call_args[0][0].method == "DELETE"


def test_an_unreachable_website_reports_nothing_rather_than_raising():
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert _client().trading_session("abc123") is None


def test_the_control_stays_hidden_until_the_website_answers(window: MainWindow) -> None:
    assert window.trading_session_badge.isHidden()
    assert window.trading_session_start.isHidden()


def test_an_unconfigured_website_never_starts_a_worker(window: MainWindow) -> None:
    window._refresh_trading_session()

    assert window._trading_session_thread is None


def test_a_running_session_offers_to_stop_it(window: MainWindow) -> None:
    window._render_trading_session(
        {"mode": "session", "active": {"started_at": "2026-10-24T20:30:00+00:00", "label": ""}}
    )

    assert window.trading_session_badge.text() == "Flipping"
    assert "2026-10-24 20:30" in window.trading_session_note.text()
    assert not window.trading_session_stop.isHidden()
    assert window.trading_session_start.isHidden()


def test_a_labelled_session_says_what_it_was_called(window: MainWindow) -> None:
    window._render_trading_session(
        {
            "mode": "session",
            "active": {"started_at": "2026-10-24T20:30:00+00:00", "label": "Evening flips"},
        }
    )

    assert "Evening flips" in window.trading_session_note.text()


def test_no_session_offers_to_start_one(window: MainWindow) -> None:
    window._render_trading_session({"mode": "session", "active": None})

    assert window.trading_session_badge.text() == "Not flipping"
    assert "Supplies" in window.trading_session_note.text()
    assert not window.trading_session_start.isHidden()
    assert not window.trading_session_backdate.isHidden()
    assert window.trading_session_stop.isHidden()


def test_the_old_tracking_mode_hides_the_control_entirely(window: MainWindow) -> None:
    window._render_trading_session({"mode": "always", "active": None})

    for widget in (
        window.trading_session_badge,
        window.trading_session_note,
        window.trading_session_start,
        window.trading_session_backdate,
        window.trading_session_stop,
    ):
        assert widget.isHidden()


def test_a_website_that_will_not_answer_says_so(window: MainWindow) -> None:
    window._trading_session_failed("The website did not answer.")

    assert window.trading_session_badge.text() == "Unknown"
    assert window.trading_session_note.text() == "The website did not answer."
    assert window.trading_session_stop.isHidden()


def test_the_control_is_actually_on_the_journal_page(window: MainWindow) -> None:
    journal_page = window.pages.widget(MainWindow.NAV_ITEMS.index("Trade Journal"))
    widgets = set(journal_page.findChildren(type(window.trading_session_start)))

    assert window.trading_session_start in widgets
    assert window.trading_session_stop in widgets
    assert window.trading_session_badge.parentWidget() is not None
