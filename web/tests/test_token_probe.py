from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

from toolkit_web.token_probe import probe_token


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.read.return_value = b'{"accounts": []}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_a_working_token_reads_true():
    with patch("urllib.request.urlopen", return_value=_ok_response()):
        assert probe_token("https://sync.example.invalid", "good-token") is True


def test_a_401_reads_as_definitely_wrong():
    error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert probe_token("https://sync.example.invalid", "bad-token") is False


def test_a_403_also_reads_as_definitely_wrong():
    error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert probe_token("https://sync.example.invalid", "bad-token") is False


def test_a_server_error_cannot_be_told_apart_from_a_wrong_token_so_it_says_neither():
    """A 500 is the service's own fault, not evidence the token was ever wrong — the pasted
    value should not be rejected over an outage that has nothing to do with it."""
    error = urllib.error.HTTPError("url", 500, "Internal Server Error", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        assert probe_token("https://sync.example.invalid", "some-token") is None


def test_an_unreachable_service_cannot_confirm_or_deny():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert probe_token("https://sync.example.invalid", "some-token") is None
